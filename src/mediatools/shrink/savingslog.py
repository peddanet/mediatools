"""Space saved per session and per day, read from the shrink logs."""
from __future__ import annotations

import os
import re
from datetime import datetime

from mediatools.shrink.savings import Savings


class SavingsLog:
    """Reads the result tables of all shrink logs and sums the saved space.

    Every shrink session writes one log ``logs/shrink_<date>_<time>.log``;
    a release switch (``mediatools reload``) continues the same log, so one
    log is one session. Each shrunk file is a row of the result table::

        2026-11-12 05:29:03,412 INFO    │  143 │ 05:29 │  1148 MB │   148 MB │ ... │ shrunk │ ...

    The timestamp of the log line gives date and time, the two MB columns
    the source and output size. Only rows with status ``shrunk`` free space
    (``HEVC``, ``kept``, copies and failures do not). Logs are read again
    only when they changed (cache by size and modification time).

    Attributes:
        folder: Folder of the logs.
        cache: Parsed rows by log path, with size and modification time.
    """

    ROW = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)(?:,\d+)? \S+\s+│\s*\d+ │\s*\d\d:\d\d │"
                     r"\s*(\d+) MB │\s*(\d+) MB │.*│\s*shrunk\s*│")
    NAME = re.compile(r"^shrink_(\d{4}-\d\d-\d\d_\d\d-\d\d-\d\d)\.log$")
    MB = 1_000_000

    def __init__(self, folder: str):
        """Choose the log folder.

        Args:
            folder: Folder with the ``shrink_*.log`` files.
        """
        self.folder = folder
        self.cache: dict[str, tuple[int, float, list[tuple[datetime, int, int]]]] = {}

    def logs(self) -> list[str]:
        """Return the shrink logs, oldest first.

        Returns:
            Paths of ``shrink_<date>_<time>.log`` files.
        """
        try:
            names = sorted(name for name in os.listdir(self.folder) if self.NAME.match(name))
        except OSError:
            return []
        return [os.path.join(self.folder, name) for name in names]

    def rows(self, path: str) -> list[tuple[datetime, int, int]]:
        """Return the shrunk files of one log.

        Args:
            path: Log file.

        Returns:
            Time, source bytes and output bytes per shrunk file.
        """
        try:
            info = os.stat(path)
        except OSError:
            return []
        cached = self.cache.get(path)
        if cached and cached[0] == info.st_size and cached[1] == info.st_mtime:
            return cached[2]
        rows = []
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                match = self.ROW.match(line)
                if match:
                    rows.append((datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S"),
                                 int(match.group(2)) * self.MB, int(match.group(3)) * self.MB))
        self.cache[path] = (info.st_size, info.st_mtime, rows)
        return rows

    @classmethod
    def started(cls, path: str) -> datetime | None:
        """Return the start of a session from its log name.

        Args:
            path: Log file.

        Returns:
            Start time, or None for another name.
        """
        match = cls.NAME.match(os.path.basename(path))
        return datetime.strptime(match.group(1), "%Y-%m-%d_%H-%M-%S") if match else None

    def session(self, path: str) -> Savings:
        """Sum one session.

        Args:
            path: Its log file.

        Returns:
            Savings of the session; ``start`` is the session start.
        """
        start = self.started(path) or datetime.now()
        result = Savings(os.path.basename(path)[:-4], start, start)
        for when, src, out in self.rows(path):
            result.add(when, src, out)
        result.start = start
        return result

    def sessions(self) -> list[Savings]:
        """Sum every session that shrank at least one file.

        Returns:
            One entry per log, oldest first.
        """
        return [item for item in map(self.session, self.logs()) if item.files]

    def days(self) -> list[Savings]:
        """Sum per calendar day of the finished files.

        Returns:
            One entry per day with shrunk files, oldest first.
        """
        result: dict[str, Savings] = {}
        for path in self.logs():
            for when, src, out in self.rows(path):
                day = when.strftime("%Y-%m-%d")
                result.setdefault(day, Savings(day, when, when)).add(when, src, out)
        return [result[day] for day in sorted(result)]

    def total(self) -> Savings:
        """Sum all sessions.

        Returns:
            Savings of all logs; ``name`` tells since when (``since <date>``).
        """
        now = datetime.now()
        result = Savings("", now, now)
        for path in self.logs():
            for when, src, out in self.rows(path):
                result.add(when, src, out)
        result.name = f"since {result.start:%Y-%m-%d}" if result.files else "nothing shrunk yet"
        return result

    @staticmethod
    def gib(size: int) -> str:
        """Format bytes in GiB.

        Args:
            size: Bytes.

        Returns:
            Text such as ``32.6``.
        """
        return f"{size / 1024 ** 3:.1f}"

    def report(self, by: str = "session") -> list[str]:
        """Build the table of ``mediatools savings``.

        Args:
            by: ``session`` (one row per log) or ``day``.

        Returns:
            Lines of a table, a total line, and a note on precision.
        """
        items = self.sessions() if by == "session" else self.days()
        head = "session" if by == "session" else "day"
        lines = [f"{head:<26} {'from':>5} {'to':>5} {'files':>6} {'read GiB':>9} "
                 f"{'written GiB':>11} {'freed GiB':>10} {'output':>7}"]
        for item in items:
            label = item.start.strftime("%Y-%m-%d %H:%M") if by == "session" else item.name
            lines.append(f"{label:<26} {item.start:%H:%M} {item.end:%H:%M} {item.files:>6} "
                         f"{self.gib(item.src):>9} {self.gib(item.out):>11} "
                         f"{self.gib(item.freed):>10} {item.share:>6.1f}%")
        total = self.total()
        lines.append(f"{'total ' + total.name:<38} {total.files:>6} {self.gib(total.src):>9} "
                     f"{self.gib(total.out):>11} {self.gib(total.freed):>10} "
                     f"{total.share:>6.1f}%")
        lines.append("sizes from the result tables of the logs (MB, rounded): exact to about "
                     "0.5 MB per file; only status 'shrunk' frees space")
        return lines
