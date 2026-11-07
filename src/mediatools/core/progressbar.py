"""One-line terminal progress display with remaining-time estimate."""
from __future__ import annotations

import sys
import time
from typing import TextIO


class ProgressBar:
    """Shows overall progress by bytes, files and estimated remaining time.

    Progress is measured in source bytes, because encoding time grows with
    file size far more than with file count. The remaining time is
    ``remaining bytes / speed``; the speed comes from this run once two
    files are done or 30 s have passed, before that from earlier runs
    (``initial_rate``).

    On a terminal the line is redrawn in place (``\\r``); otherwise (output
    redirected to a file) nothing is drawn and only the log is written.

    Attributes:
        total_bytes: Bytes of all files of the run.
        total_files: Number of files of the run.
        done_bytes: Bytes of finished files.
        done_files: Number of finished files.
        initial_rate: Bytes per second from earlier runs, or None.
        stream: Output stream.
        started: Start time (monotonic seconds).
        enabled: True if the stream is a terminal.
    """

    WIDTH = 24
    MIN_ELAPSED = 30.0

    def __init__(self, total_bytes: int, total_files: int, initial_rate: float | None = None,
                 stream: TextIO | None = None):
        """Start the display.

        Args:
            total_bytes: Bytes of all files of the run.
            total_files: Number of files of the run.
            initial_rate: Bytes per second from earlier runs, or None.
            stream: Output stream; ``sys.stderr`` if None.
        """
        self.total_bytes = max(total_bytes, 1)
        self.total_files = total_files
        self.done_bytes = 0
        self.done_files = 0
        self.initial_rate = initial_rate
        self.stream = stream or sys.stderr
        self.started = time.monotonic()
        self.enabled = hasattr(self.stream, "isatty") and self.stream.isatty()

    def rate(self, current_bytes: float = 0.0) -> float | None:
        """Return the speed used for the estimate.

        Args:
            current_bytes: Bytes of the current file already encoded.

        Returns:
            Bytes per second, or None if unknown.
        """
        elapsed = time.monotonic() - self.started
        processed = self.done_bytes + current_bytes
        if (self.done_files >= 2 or elapsed >= self.MIN_ELAPSED) and processed > 0:
            return processed / elapsed
        return self.initial_rate

    def remaining_seconds(self, current_bytes: float = 0.0) -> float | None:
        """Estimate the remaining time.

        Args:
            current_bytes: Bytes of the current file already encoded.

        Returns:
            Seconds, or None if no speed is known.
        """
        rate = self.rate(current_bytes)
        if not rate:
            return None
        return max(0.0, self.total_bytes - self.done_bytes - current_bytes) / rate

    def line(self, name: str = "", file_fraction: float = 0.0, file_bytes: int = 0) -> str:
        """Build the status line.

        Args:
            name: Name of the current file.
            file_fraction: Encoded fraction of the current file (0..1).
            file_bytes: Size of the current file.

        Returns:
            Text without line break.
        """
        current = file_fraction * file_bytes
        fraction = min(1.0, (self.done_bytes + current) / self.total_bytes)
        filled = int(fraction * self.WIDTH)
        remaining = self.remaining_seconds(current)
        eta = "?" if remaining is None else self.duration_text(remaining)
        short = name if len(name) <= 30 else name[:12] + "..." + name[-15:]
        return (f"[{'#' * filled}{'.' * (self.WIDTH - filled)}] {fraction * 100:5.1f} % | "
                f"{self.done_files}/{self.total_files} files | "
                f"{(self.done_bytes + current) / 1024 ** 3:.1f}/"
                f"{self.total_bytes / 1024 ** 3:.1f} GiB | ETA {eta} | "
                f"{file_fraction * 100:3.0f} % {short}")

    def show(self, name: str = "", file_fraction: float = 0.0, file_bytes: int = 0) -> None:
        """Redraw the status line on a terminal.

        Args:
            name: Name of the current file.
            file_fraction: Encoded fraction of the current file (0..1).
            file_bytes: Size of the current file.
        """
        if self.enabled:
            self.stream.write("\r\033[K" + self.line(name, file_fraction, file_bytes))
            self.stream.flush()

    def file_done(self, size: int) -> None:
        """Count a finished (or skipped) file.

        Args:
            size: Its source size in bytes.
        """
        self.done_files += 1
        self.done_bytes += size

    def clear(self) -> None:
        """Remove the status line before a log message is printed."""
        if self.enabled:
            self.stream.write("\r\033[K")
            self.stream.flush()

    @staticmethod
    def duration_text(seconds: float) -> str:
        """Format a duration compactly.

        Args:
            seconds: Duration in seconds.

        Returns:
            Text such as ``2d03h``, ``5h12m`` or ``42m``.
        """
        minutes = int(seconds // 60)
        hours, minutes = divmod(minutes, 60)
        days, hours = divmod(hours, 24)
        if days:
            return f"{days}d{hours:02d}h"
        if hours:
            return f"{hours}h{minutes:02d}m"
        return f"{minutes}m"
