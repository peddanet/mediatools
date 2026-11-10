"""Trial decode that tells whether a video's data can be decoded at all."""
from __future__ import annotations

import shutil
import subprocess


class DecodeCheck:
    """Decodes the start of a video and counts the decoder errors.

    Some videos have an intact container (``ftyp``/``mdat``/``moov``,
    plausible duration, codec ``avc1``) but sample data a decoder cannot
    use: still encrypted after the encryption boxes were stripped, or
    scrambled. Encoding such a file takes as long as a good one and yields a
    grey or empty result. A short trial decode finds them in seconds: a
    healthy video decodes without errors, an unusable one produces errors
    from the first frame on (``decode_slice_header error``, ``no frame!``,
    ``non-existing PPS``, AAC ``channel element ... is not allocated``).

    :meth:`verify` decodes a whole file and accepts it only without a
    single error; ``shrink`` uses it on every encoded output before the
    original is replaced.

    The check reads only; it never writes or changes the file.

    Attributes:
        ffmpeg: Name or path of the ffmpeg executable.
        seconds: Length of the trial decode from the start, in seconds.
        max_errors: A file with at least this many error lines is unusable.
        timeout: Seconds after which the trial is abandoned (result unknown).
    """

    SECONDS = 10.0
    MAX_ERRORS = 10
    TIMEOUT = 300.0

    def __init__(self, ffmpeg: str = "ffmpeg", seconds: float = SECONDS,
                 max_errors: int = MAX_ERRORS, timeout: float = TIMEOUT) -> None:
        """Store the settings.

        Args:
            ffmpeg: Name or path of the ffmpeg executable.
            seconds: Length of the trial decode, in seconds.
            max_errors: Error lines from which a file counts as unusable.
            timeout: Seconds after which the trial is abandoned.

        Raises:
            ValueError: If ``seconds`` or ``timeout`` is not positive or
                ``max_errors`` is below 1.
        """
        if seconds <= 0 or timeout <= 0 or max_errors < 1:
            raise ValueError("seconds and timeout must be positive, max_errors at least 1")
        self.ffmpeg = ffmpeg
        self.seconds = seconds
        self.max_errors = max_errors
        self.timeout = timeout

    def command(self, path: str) -> list[str]:
        """Build the ffmpeg command of the trial decode.

        All streams are decoded (``-f null``: nothing is written); ``-t``
        before ``-i`` limits the input to the first :attr:`seconds`.

        Args:
            path: Video file.

        Returns:
            Argument list for :func:`subprocess.run`.
        """
        return [self.ffmpeg, "-hide_banner", "-nostdin", "-v", "error",
                "-t", f"{self.seconds:g}", "-i", path, "-f", "null", "-"]

    def verify(self, path: str, timeout: float, niceness: int = 19) -> str | None:
        """Decode a whole file and report the first problem.

        Unlike :meth:`unusable`, an unknown result is a failure here: the
        caller keeps the original unless the file decoded completely and
        without any error line.

        Args:
            path: Video file.
            timeout: Seconds after which the decode is abandoned.
            niceness: Scheduling priority (0 normal, 19 lowest), as for the
                encoder.

        Returns:
            None if every stream decoded without error, otherwise the reason.
        """
        if shutil.which(self.ffmpeg) is None:
            return f"{self.ffmpeg} not found"
        cmd = [self.ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-i", path,
               "-f", "null", "-"]
        if shutil.which("nice") is not None:
            cmd = ["nice", "-n", str(niceness), *cmd]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                                 check=False, timeout=timeout)
        except subprocess.TimeoutExpired:
            return f"full decode did not finish within {timeout:.0f} s"
        lines = [line.strip() for line in res.stderr.splitlines() if line.strip()]
        if lines:
            return f"{len(lines)} decoder error line(s), first: {lines[0][:200]}"
        if res.returncode != 0:
            return f"ffmpeg exit code {res.returncode}"
        return None

    def errors(self, path: str) -> int | None:
        """Count the error lines of a trial decode.

        Args:
            path: Video file.

        Returns:
            Number of non-empty lines ffmpeg wrote at log level ``error``,
            or None if ffmpeg is missing or the trial timed out.
        """
        if shutil.which(self.ffmpeg) is None:
            return None
        try:
            res = subprocess.run(self.command(path), capture_output=True, text=True,
                                 errors="replace", check=False, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            return None
        return sum(1 for line in res.stderr.splitlines() if line.strip())

    def unusable(self, path: str) -> int | None:
        """Tell whether a video's data cannot be decoded.

        Args:
            path: Video file.

        Returns:
            The error count if it reaches :attr:`max_errors` (the file is
            unusable), otherwise None - also when the trial could not run,
            so an unknown result never blocks a file.
        """
        count = self.errors(path)
        return count if count is not None and count >= self.max_errors else None
