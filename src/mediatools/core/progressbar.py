"""Two-line terminal progress display with remaining-time estimates."""
from __future__ import annotations

import os
import shutil
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

    Two lines are drawn: the overall progress (:meth:`line`) and the
    current file (:meth:`file_line`: its percentage, size, encoded part and
    remaining time). On a terminal both are redrawn in place and cut to the
    terminal width, so they never wrap; otherwise (output redirected to a
    file) nothing is drawn and only the log is written.

    Attributes:
        total_bytes: Bytes of all files of the run.
        total_files: Number of files of the run.
        done_bytes: Bytes of finished files.
        done_files: Number of finished files.
        initial_rate: Bytes per second from earlier runs, or None.
        stream: Output stream.
        started: Start time (monotonic seconds).
        enabled: True if the stream is a terminal.
        note: Text shown at the start of the first line, e.g. a pending stop.
        drawn: True while the two lines are on the screen.
        status_path: If set, both lines are also written to this file (at
            most once per :attr:`STATUS_SECONDS`), for ``mediatools attach``.
        last_status: Monotonic time of the last status file update.
    """

    WIDTH = 24
    MIN_ELAPSED = 30.0
    STATUS_SECONDS = 1.0

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
        self.note = ""
        self.drawn = False
        self.status_path: str | None = None
        self.last_status = 0.0

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

    def line(self, _name: str = "", file_fraction: float = 0.0, file_bytes: int = 0) -> str:
        """Build the line of the overall progress.

        The name of the current file is not part of this line (see
        :meth:`file_line`); the parameter keeps both methods interchangeable.

        Args:
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
        prefix = f"{self.note} | " if self.note else ""
        gauge = '#' * filled + '.' * (self.WIDTH - filled)
        return (f"{prefix}total [{gauge}] {fraction * 100:5.1f} % | "
                f"{self.done_files}/{self.total_files} files | "
                f"{(self.done_bytes + current) / 1024 ** 3:.1f}/"
                f"{self.total_bytes / 1024 ** 3:.1f} GiB | ETA {eta}")

    def file_line(self, name: str = "", file_fraction: float = 0.0, file_bytes: int = 0,
                  width: int | None = None) -> str:
        """Build the line of the current file.

        Args:
            name: Name of the current file.
            file_fraction: Encoded fraction of the current file (0..1).
            file_bytes: Size of the current file.
            width: Maximum length; a longer name is shortened in the middle,
                so that its start and end stay visible. None: no limit.

        Returns:
            Text without line break, e.g.
            ``file  35 % of 2.40 GiB (0.84 GiB encoded) | 12m left | clip.mp4``.
        """
        current = file_fraction * file_bytes
        rate = self.rate(current)
        left = "?" if not rate else self.duration_text((file_bytes - current) / rate)
        head = (f"file  {file_fraction * 100:3.0f} % of {file_bytes / 1024 ** 3:.2f} GiB "
                f"({current / 1024 ** 3:.2f} GiB encoded) | {left} left | ")
        room = None if width is None else max(9, width - len(head))
        if room is not None and len(name) > room:
            keep = room - 3
            name = name[:keep // 2] + "..." + name[len(name) - (keep - keep // 2):]
        return head + name

    def _write_status(self, name: str, file_fraction: float, file_bytes: int) -> None:
        """Replace the status file with both lines (rate-limited, atomic).

        Args:
            name: Name of the current file.
            file_fraction: Encoded fraction of the current file (0..1).
            file_bytes: Size of the current file.
        """
        now = time.monotonic()
        if self.status_path is None or now - self.last_status < self.STATUS_SECONDS:
            return
        self.last_status = now
        text = (self.line(name, file_fraction, file_bytes) + "\n"
                + self.file_line(name, file_fraction, file_bytes) + "\n")
        tmp = self.status_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                handle.write(text)
            os.replace(tmp, self.status_path)
        except OSError:
            pass                                 # the display must never stop a run

    def show(self, name: str = "", file_fraction: float = 0.0, file_bytes: int = 0) -> None:
        """Redraw both status lines on a terminal.

        Args:
            name: Name of the current file.
            file_fraction: Encoded fraction of the current file (0..1).
            file_bytes: Size of the current file.
        """
        self._write_status(name, file_fraction, file_bytes)
        if not self.enabled:
            return
        width = max(20, shutil.get_terminal_size().columns - 1)
        first = self.line(name, file_fraction, file_bytes)[:width]
        second = self.file_line(name, file_fraction, file_bytes, width)[:width]
        up = "\033[1A" if self.drawn else ""     # back to the first line
        self.stream.write(f"\r{up}\033[K{first}\n\033[K{second}")
        self.stream.flush()
        self.drawn = True

    def file_done(self, size: int) -> None:
        """Count a finished (or skipped) file.

        Args:
            size: Its source size in bytes.
        """
        self.done_files += 1
        self.done_bytes += size

    def file_skipped(self, size: int) -> None:
        """Remove a file that needed no encoding from the totals.

        Such files (already HEVC, recorded meanwhile) cost no encoding time;
        counting them as done would inflate progress and speed.

        Args:
            size: Its source size in bytes.
        """
        self.total_files = max(0, self.total_files - 1)
        self.total_bytes = max(1, self.total_bytes - size)

    def clear(self) -> None:
        """Remove both status lines before a log message is printed."""
        if self.enabled and self.drawn:
            self.stream.write("\r\033[K\033[1A\r\033[K")
            self.stream.flush()
            self.drawn = False

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
