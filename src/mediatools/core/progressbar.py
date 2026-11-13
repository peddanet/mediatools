"""Two-line terminal progress display with remaining-time estimates."""
from __future__ import annotations

import os
import shutil
import sys
import time
from typing import TextIO

from mediatools.core.progressbase import ProgressBase
from mediatools.core.statusfile import StatusFile


class ProgressBar:
    """Shows overall progress by bytes, files and estimated remaining time.

    Progress is measured in source bytes, because encoding time grows with
    file size far more than with file count. The remaining time is
    ``remaining bytes / speed``; the speed comes from this run once two
    files are done or 30 s have passed, before that from earlier runs
    (``initial_rate``).

    Two lines are drawn: the overall progress (:meth:`line`) and the
    current file (:meth:`file_line`: its percentage, size, encoded part and
    remaining time). A caller may add a third line (``extra`` of
    :meth:`show`), e.g. the space read, written and freed. On a terminal the
    lines are redrawn in place and cut to the terminal width, so they never
    wrap; otherwise (output redirected to a file) nothing is drawn and only
    the log is written.

    The first line counts *source* bytes of the whole job (since 1.4.2):
    how much was read and encoded since the first shrink log, with the
    size after shrinking in brackets, and how much is still to go;
    percentage and ETA follow from these (:attr:`base`). A start or a
    reload therefore does not reset the figures.

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
        drawn: Number of lines on the screen (0: nothing drawn).
        status: Status file for ``mediatools attach`` (lines written at most
            once per :attr:`STATUS_SECONDS`); see :attr:`status_path`.
        base: Work of all shrink runs before this process.
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
        self.drawn = 0
        self.status = StatusFile()
        self.base = ProgressBase()

    @property
    def status_path(self) -> str | None:
        """Status file, or None (the lines are then only drawn)."""
        return self.status.path

    @status_path.setter
    def status_path(self, path: str | None) -> None:
        self.status.path = path

    @property
    def last_status(self) -> float:
        """Monotonic time of the last status file update."""
        return self.status.last

    @last_status.setter
    def last_status(self, when: float) -> None:
        self.status.last = when

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
        gib = 1024 ** 3
        current = file_fraction * file_bytes
        read = self.base.read + self.done_bytes + current
        to_go = max(0.0, self.total_bytes - self.done_bytes - current)
        fraction = min(1.0, read / max(1.0, read + to_go))
        filled = int(fraction * self.WIDTH)
        remaining = self.remaining_seconds(current)
        eta = "?" if remaining is None else self.duration_text(remaining)
        prefix = f"{self.note} | " if self.note else ""
        gauge = '#' * filled + '.' * (self.WIDTH - filled)
        after = (f" (-> {self.base.after() / gib:.1f} GiB after shrinking)"
                 if self.base.after is not None else "")
        since = f"since {self.base.since}: " if self.base.since else ""
        return (f"{prefix}total [{gauge}] {fraction * 100:5.1f} % | ETA {eta} | {since}"
                f"{read / gib:.1f} GiB read{after}, {to_go / gib:.1f} GiB to go | "
                f"{self.base.files + self.done_files} files done, "
                f"{max(0, self.total_files - self.done_files)} to go")

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

    def _write_status(self, name: str, file_fraction: float, file_bytes: int,
                      extra: str = "") -> None:
        """Replace the status file with all lines (rate-limited, atomic).

        Args:
            name: Name of the current file.
            file_fraction: Encoded fraction of the current file (0..1).
            file_bytes: Size of the current file.
            extra: Optional third line; empty for none.
        """
        now = time.monotonic()
        if self.status.path is None or now - self.status.last < self.STATUS_SECONDS:
            return
        self.status.last = now
        text = (self.line(name, file_fraction, file_bytes) + "\n"
                + self.file_line(name, file_fraction, file_bytes) + "\n"
                + (extra + "\n" if extra else ""))
        tmp = self.status.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                handle.write(text)
            os.replace(tmp, self.status.path)
        except OSError:
            pass                                 # the display must never stop a run

    def show(self, name: str = "", file_fraction: float = 0.0, file_bytes: int = 0,
             extra: str = "") -> None:
        """Redraw the status lines on a terminal.

        Args:
            name: Name of the current file.
            file_fraction: Encoded fraction of the current file (0..1).
            file_bytes: Size of the current file.
            extra: Optional third line; empty for none.
        """
        self._write_status(name, file_fraction, file_bytes, extra)
        if not self.enabled:
            return
        width = max(20, shutil.get_terminal_size().columns - 1)
        lines = [self.line(name, file_fraction, file_bytes)[:width],
                 self.file_line(name, file_fraction, file_bytes, width)[:width]]
        if extra:
            lines.append(extra[:width])
        up = "\033[1A" * (self.drawn - 1) if self.drawn > 1 else ""   # back to line 1
        if len(lines) < self.drawn:              # fewer lines than before: clear first
            self.clear()
            up = ""
        self.stream.write("\r" + up + "\n".join("\033[K" + text for text in lines))
        self.stream.flush()
        self.drawn = len(lines)

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
        """Remove the status lines before a log message is printed."""
        if self.enabled and self.drawn:
            self.stream.write("\r\033[K" + "\033[1A\r\033[K" * (self.drawn - 1))
            self.stream.flush()
            self.drawn = 0

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
