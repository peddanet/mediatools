"""HEVC encoding with result validation."""
from __future__ import annotations

import os
import signal
import subprocess
from collections.abc import Callable

from mediatools.core.probe import MediaProbe


class HevcEncoder:
    """Encodes a video with ffmpeg/libx265 and validates the result.

    The output is accepted only if ffmpeg exits with 0, the file is not
    empty, it contains a video stream and its duration matches the input
    within ``max(TOL_ABS, duration * TOL_REL)`` seconds.

    ffmpeg runs in its own session (process group): a Ctrl+C in the
    terminal reaches only mediatools, which decides whether the current
    encode finishes or is stopped with :meth:`abort`. Progress is read from
    ``-progress pipe:1``; ffmpeg's messages go to ``<dst>.ffmpeg.log`` and
    are logged on failure.

    Attributes:
        crf: libx265 constant rate factor.
        threads: ffmpeg ``-threads`` value.
        probe: :class:`MediaProbe` used for validation.
        log: Optional logger.
        ffmpeg: Name or path of the ffmpeg executable.
        process: Running ffmpeg process, if any.
        aborted: True after :meth:`abort` during the current encode.
    """

    TOL_ABS = 1.0
    TOL_REL = 0.02

    def __init__(self, crf: int = 28, threads: int = 6, probe: MediaProbe | None = None,
                 logger=None, ffmpeg: str = "ffmpeg"):
        """Configure the encoder.

        Args:
            crf: libx265 constant rate factor (28 is the libx265 default).
            threads: ffmpeg ``-threads`` value.
            probe: Probe for validation; a new one if None.
            logger: Optional :class:`logging.Logger`.
            ffmpeg: Name or path of the ffmpeg executable.
        """
        self.crf = crf
        self.threads = threads
        self.probe = probe or MediaProbe()
        self.log = logger
        self.ffmpeg = ffmpeg
        self.process: subprocess.Popen | None = None
        self.aborted = False

    def command(self, src: str, dst: str) -> list[str]:
        """Build the ffmpeg argument list (no shell is involved).

        Args:
            src: Input video.
            dst: Output file.

        Returns:
            Argument list for :func:`subprocess.run`.
        """
        return [self.ffmpeg, "-nostdin", "-y", "-hide_banner", "-nostats",
                "-progress", "pipe:1", "-threads", str(self.threads), "-i", src,
                "-vcodec", "libx265", "-crf", str(self.crf), dst]

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
        os.remove(log_path)
        if not os.path.isfile(dst) or os.path.getsize(dst) == 0:
            self._error("no output produced, original kept: %s", src)
            return False
        return self.validate(src, dst)

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
        """Compare video stream and duration of the output with the input.

        Args:
            src: Input video.
            dst: Encoded output.

        Returns:
            True if the output has a video stream and a matching duration
            (or if the duration of either file cannot be determined).
        """
        if not self.probe.has_video(dst):
            self._error("output has no video stream, original kept: %s", src)
            return False
        d_in, d_out = self.probe.duration(src), self.probe.duration(dst)
        if d_in is None or d_out is None:
            self._info("duration unknown (in=%s, out=%s), accepted on stream check", d_in, d_out)
            return True
        tolerance = max(self.TOL_ABS, d_in * self.TOL_REL)
        if abs(d_in - d_out) > tolerance:
            self._error("duration mismatch (in=%.2fs, out=%.2fs), original kept: %s",
                        d_in, d_out, src)
            return False
        self._info("output validated (in=%.2fs, out=%.2fs)", d_in, d_out)
        return True

    def _info(self, *args) -> None:
        """Log at INFO level if a logger is set.

        Args:
            *args: Format string and arguments for :meth:`logging.Logger.info`.
        """
        if self.log:
            self.log.info(*args)

    def _error(self, *args) -> None:
        """Log at ERROR level if a logger is set.

        Args:
            *args: Format string and arguments for :meth:`logging.Logger.error`.
        """
        if self.log:
            self.log.error(*args)
