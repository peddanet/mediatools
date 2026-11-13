"""Location and last update of the progress status file."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StatusFile:
    """Where the progress lines are mirrored for ``mediatools attach``.

    Attributes:
        path: Status file, or None for none.
        last: Monotonic time of the last update.
    """

    path: str | None = None
    last: float = 0.0
