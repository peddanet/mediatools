"""Shrink work done before this process, as the start of the progress line."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class ProgressBase:
    """What all shrink runs had already read when this process started.

    The first progress line shows the whole job: everything read since the
    first shrink log (``since``) plus what this process reads, against what
    is still to go. Without this base the line would only count the current
    process and start at zero after every start or ``mediatools reload``.

    Attributes:
        read: Source bytes encoded before this process (all shrink logs).
        files: Files encoded before this process.
        since: Date of the first shrink log, e.g. ``2026-11-09``; empty if none.
        after: Returns the size after shrinking of everything shrunk so far,
            or None to leave that figure out.
    """

    read: int = 0
    files: int = 0
    since: str = ""
    after: Callable[[], int] | None = None
