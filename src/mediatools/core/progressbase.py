"""Work done for the job before this process, as the start of the progress line."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class ProgressBase:
    """What earlier runs had already read of this job when this process started.

    The first progress line shows the whole job: everything read of it
    since its first finished file (``since``) plus what this process reads,
    against what is still to go. Without this base the line would only
    count the current process and start at zero after every start or
    ``mediatools reload``. Since 1.6.0 the figures come from the ledger and
    only count files of the job (root and filters, :class:`JobSpec`).

    Attributes:
        read: Source bytes of the job encoded before this process.
        files: Files of the job encoded before this process.
        since: Date of the job's first finished file, e.g. ``2026-11-09``;
            empty if none.
        after: Returns the size after shrinking of the job's shrunk files,
            or None to leave that figure out.
        written: Output bytes of the job's shrunk files before this process.
    """

    read: int = 0
    files: int = 0
    since: str = ""
    after: Callable[[], int] | None = None
    written: int = 0
