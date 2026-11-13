"""HEVC encoding with result validation."""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from collections.abc import Callable

from mediatools.core.decodecheck import DecodeCheck
from mediatools.core.probe import MediaProbe


class HevcEncoder:
    """Encodes a video with ffmpeg/libx265 and validates the result.

    The output is accepted only if ffmpeg exits with 0, the file is not
    empty, it is at least :attr:`MIN_RATIO` of the input's size, it contains
    a video stream, its duration matches the input within
    ``max(TOL_ABS, duration * TOL_REL)`` seconds and - with a
    :class:`DecodeCheck` set - the whole output decodes without a single
    error (:meth:`DecodeCheck.verify`; time limit
    ``max(VERIFY_MIN, duration * VERIFY_FACTOR)``). A
    suspiciously small output is typical of an input whose data could not
    be decoded: the encoder then writes almost nothing but grey frames.

    **CPU use.** libx265 runs its own thread pool, which by default uses
    every logical CPU; ffmpeg's ``-threads`` does not limit it. Both the
    decoder (``-threads``) and the x265 pool (``-x265-params pools=N``) are
    therefore set to ``threads``, by default one less than the number of
    logical CPUs, so that one CPU stays free. In addition ffmpeg runs with
    the lowest CPU priority (``nice`` 19): it still uses every idle CPU,
    but yields at once when the user works, so the desktop stays
    responsive.

    ffmpeg runs in its own session (process group): a Ctrl+C in the
    terminal reaches only mediatools, which decides whether the current
    encode finishes or is stopped with :meth:`abort`. Progress is read from
    ``-progress pipe:1``; ffmpeg's messages go to ``<dst>.ffmpeg.log``; the
    file is deleted after a validated encode and kept otherwise (its last
    lines are logged; the caller decides where it is kept).

    Attributes:
        crf: libx265 constant rate factor.
        threads: Decoder threads and size of the x265 thread pool.
        niceness: Scheduling priority of ffmpeg (0 normal, 19 lowest).
        probe: :class:`MediaProbe` used for validation.
        log: Optional logger.
        decode: Trial decode of the output, or None (no decode check).
        ffmpeg: Name or path of the ffmpeg executable.
        process: Running ffmpeg process, if any.
        aborted: True after :meth:`abort` during the current encode.
    """

    TOL_ABS = 1.0
    TOL_REL = 0.02
    MIN_RATIO = 0.02
    VERIFY_MIN = 600.0
    VERIFY_FACTOR = 2.0

    NICENESS = 19

    def __init__(self, crf: int = 28, threads: int | None = None,
                 probe: MediaProbe | None = None, logger=None, ffmpeg: str = "ffmpeg",
                 niceness: int = NICENESS, decode: DecodeCheck | None = None):
        """Configure the encoder.

        Args:
            crf: libx265 constant rate factor (28 is the libx265 default).
            threads: Decoder threads and x265 pool size; None uses
                :meth:`default_threads`.
            probe: Probe for validation; a new one if None.
            logger: Optional :class:`logging.Logger`.
            ffmpeg: Name or path of the ffmpeg executable.
            niceness: Scheduling priority of ffmpeg (0 normal, 19 lowest).
            decode: Trial decode of the output, or None (no decode check).
        """
        self.decode = decode
        self.crf = crf
        self.niceness = niceness
        self.threads = threads if threads and threads > 0 else self.default_threads()
        self.probe = probe or MediaProbe()
        self.log = logger
        self.ffmpeg = ffmpeg
        self.process: subprocess.Popen | None = None
        self.aborted = False

    def clone(self) -> HevcEncoder:
        """Return an encoder with the same settings for a parallel slot.

        Returns:
            A new encoder without a running process.
        """
        return HevcEncoder(self.crf, self.threads, self.probe, self.log, self.ffmpeg,
                           self.niceness, self.decode)

    @staticmethod
    def default_threads() -> int:
        """Return the default thread count: logical CPUs minus one.

        Returns:
            At least 1.
        """
        return max(1, (os.cpu_count() or 2) - 1)

    def command(self, src: str, dst: str) -> list[str]:
        """Build the ffmpeg argument list (no shell is involved).

        Args:
            src: Input video.
            dst: Output file.

        Returns:
            Argument list for :func:`subprocess.run`; prefixed with
            ``nice -n N`` when a niceness is set and ``nice`` is available.
        """
        prefix: list[str] = []
        nice = shutil.which("nice")
        if self.niceness > 0 and nice:
            prefix = [nice, "-n", str(self.niceness)]   # exec's ffmpeg: same process
        return prefix + [
            self.ffmpeg, "-nostdin", "-y", "-hide_banner", "-nostats",
            "-progress", "pipe:1", "-threads", str(self.threads), "-i", src,
            "-vcodec", "libx265", "-crf", str(self.crf),
            "-x265-params", f"pools={self.threads}", dst]

    def encode(self, src: str, dst: str,
               on_progress: Callable[[float], None] | None = None) -> bool:
        """Encode a video and validate the result.

        Args:
            src: Input video.
            dst: Output file (overwritten).
            on_progress: Called with the encoded fraction (0..1) of the file.

        Returns:
            True only for a validated result; False on error or abort.
        """
        cmd = self.command(src, dst)
        self._info("ffmpeg: %s", " ".join(cmd))
        duration = self.probe.duration(src) or 0.0
        log_path = dst + ".ffmpeg.log"
        self.aborted = False
        with open(log_path, "w", encoding="utf-8", errors="replace") as ff_log:
            # own session: terminal signals do not reach ffmpeg directly
            with subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=ff_log, text=True, start_new_session=True) as proc:
                self.process = proc
                for line in proc.stdout or []:
                    if on_progress and duration > 0 and line.startswith("out_time_us="):
                        value = line.split("=", 1)[1].strip()
                        if value.isdigit():
                            on_progress(min(1.0, int(value) / 1e6 / duration))
                returncode = proc.wait()
                self.process = None
        if self.aborted:
            self._info("encoding aborted, original kept: %s", src)
            return False
        if returncode != 0:
            self._error("ffmpeg exit code %i, original kept: %s", returncode, src)
            self._log_tail(log_path)
            return False
        if not os.path.isfile(dst) or os.path.getsize(dst) == 0:
            self._error("no output produced, original kept: %s", src)
            return False
        if not self.validate(src, dst):
            return False
        os.remove(log_path)
        return True

    def abort(self) -> None:
        """Stop a running encode (SIGTERM to ffmpeg's process group)."""
        proc = self.process
        if proc is None or proc.poll() is not None:
            return
        self.aborted = True
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    def _log_tail(self, log_path: str, lines: int = 8) -> None:
        """Log the last lines of ffmpeg's message file.

        Args:
            log_path: ffmpeg message file.
            lines: Number of lines to log.
        """
        try:
            with open(log_path, encoding="utf-8", errors="replace") as handle:
                tail = handle.read().splitlines()[-lines:]
        except OSError:
            return
        for text in tail:
            self._error("ffmpeg: %s", text)

    def validate(self, src: str, dst: str) -> bool:
        """Check size, video stream, duration and decodability of the output.

        Args:
            src: Input video.
            dst: Encoded output.

        Returns:
            True if the output is at least :attr:`MIN_RATIO` of the input,
            has a video stream, a matching duration (or the duration of
            either file cannot be determined) and - with a decode check -
            decodes completely without errors.
        """
        try:
            in_size, out_size = os.path.getsize(src), os.path.getsize(dst)
        except OSError:
            in_size = out_size = 0                   # sizes unknown: not compared
        if in_size > 0 and out_size < in_size * self.MIN_RATIO:
            self._error("output only %.2f %% of the source (minimum %.0f %%), original kept: %s",
                        100 * out_size / in_size, 100 * self.MIN_RATIO, src)
            return False
        if not self.probe.has_video(dst):
            self._error("output has no video stream, original kept: %s", src)
            return False
        d_in, d_out = self.probe.duration(src), self.probe.duration(dst)
        if d_in is None or d_out is None:
            self._info("duration unknown (in=%s, out=%s), not compared", d_in, d_out)
        elif abs(d_in - d_out) > max(self.TOL_ABS, d_in * self.TOL_REL):
            self._error("duration mismatch (in=%.2fs, out=%.2fs), original kept: %s",
                        d_in, d_out, src)
            return False
        if self.decode is not None:
            limit = max(self.VERIFY_MIN, (d_out or 0.0) * self.VERIFY_FACTOR)
            started = time.monotonic()
            problem = self.decode.verify(dst, limit, self.niceness)
            if problem is not None:
                self._error("output failed the full decode (%s), original kept: %s", problem, src)
                return False
            self._info("output decoded completely without errors in %.0f s",
                       time.monotonic() - started)
        self._info("output validated (in=%s s, out=%s s)", d_in, d_out)
        return True

    def _info(self, *args) -> None:
        """Log details at DEBUG level (log file only) if a logger is set.

        Args:
            *args: Format string and arguments for :meth:`logging.Logger.debug`.
        """
        if self.log:
            self.log.debug(*args)

    def _error(self, *args) -> None:
        """Log at ERROR level if a logger is set.

        Args:
            *args: Format string and arguments for :meth:`logging.Logger.error`.
        """
        if self.log:
            self.log.error(*args)
