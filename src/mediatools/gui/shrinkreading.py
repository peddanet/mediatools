"""One reading of the shrink run, as the GUI shows it."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ShrinkReading:
    """One reading of the shrink run.

    Attributes:
        pid: Process ID of the running shrink, or None.
        report: Lines of ``mediatools status``.
        progress: Progress lines of the status file (two or three).
        percent: Overall percentage from the first progress line, or None.
        log_path: Log file of the run, or None.
        overall: Space line: read, written and freed in this (or the last)
            session and in all sessions.
    """

    pid: int | None = None
    report: list[str] = field(default_factory=list)
    progress: list[str] = field(default_factory=list)
    percent: float | None = None
    log_path: str | None = None
    overall: str = ""
