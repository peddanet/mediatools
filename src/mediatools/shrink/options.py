"""Settings of a shrink run."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ShrinkOptions:
    """What a :class:`Shrinker` run processes and how.

    Attributes:
        keep_copies: Do not replace originals and record nothing; the shrunk
            copies stay in ``newShrinkedFiles/`` for review.
        extensions: Extensions to process (lower case, with dot).
        db_name: Database file name; None uses the default name.
        order: ``largest`` (default), ``smallest`` or ``path``.
        min_seconds: Leave out videos shorter than this; 0 processes all.
    """

    keep_copies: bool = False
    extensions: frozenset = field(default_factory=lambda: frozenset({".mp4"}))
    db_name: str | None = None
    order: str = "largest"
    min_seconds: float = 0.0

    ORDERS = ("largest", "smallest", "path")

    def __post_init__(self) -> None:
        """Validate the values.

        Raises:
            ValueError: If ``order`` is unknown or ``min_seconds`` negative.
        """
        if self.order not in self.ORDERS:
            raise ValueError(f"order must be one of {self.ORDERS}")
        if self.min_seconds < 0:
            raise ValueError("min_seconds must not be negative")
