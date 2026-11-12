"""Space saved by shrinking in one period (a session, a day, all time)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class Savings:
    """Read, written and freed space of the files shrunk in a period.

    Sizes come from the result tables of the shrink logs, which give them in
    MB (10^6 bytes, rounded); a sum is therefore exact to about 0.5 MB per
    file.

    Attributes:
        name: Label of the period: log name of a session, date of a day.
        start: Time of the first shrunk file (or of the session start).
        end: Time of the last shrunk file.
        files: Number of shrunk files.
        src: Source bytes (read).
        out: Output bytes (written).
    """

    name: str
    start: datetime
    end: datetime
    files: int = 0
    src: int = 0
    out: int = 0

    @property
    def freed(self) -> int:
        """Bytes returned to the file system: source minus output."""
        return self.src - self.out

    @property
    def share(self) -> float:
        """Output in percent of the source (0 if nothing was read)."""
        return self.out / self.src * 100 if self.src else 0.0

    def add(self, when: datetime, src: int, out: int) -> None:
        """Count one shrunk file.

        Args:
            when: Time the file was finished.
            src: Source bytes.
            out: Output bytes.
        """
        self.files += 1
        self.src += src
        self.out += out
        self.start = min(self.start, when)
        self.end = max(self.end, when)
