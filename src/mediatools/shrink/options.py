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
        retry_failed: Include videos recorded as failed (``FAILED`` or
            ``BROKEN``) in an earlier run; by default they are left out
            while their size is unchanged.
        min_size: Leave out videos smaller than this many bytes; 0 processes all.
        newer_than: Leave out videos modified before this time (epoch
            seconds); None processes all.
        jobs: Number of videos encoded at the same time (1: one after the other).
    """

    keep_copies: bool = False
    extensions: frozenset = field(default_factory=lambda: frozenset({".mp4"}))
    db_name: str | None = None
    order: str = "largest"
    min_seconds: float = 0.0
    retry_failed: bool = False
    min_size: int = 0
    newer_than: float | None = None
    jobs: int = 1

    ORDERS = ("largest", "smallest", "path")

    def __post_init__(self) -> None:
        """Validate the values.

        Raises:
            ValueError: If ``order`` is unknown, ``min_seconds`` or ``min_size``
                negative or ``jobs`` below 1.
        """
        if self.order not in self.ORDERS:
            raise ValueError(f"order must be one of {self.ORDERS}")
        if self.min_seconds < 0:
            raise ValueError("min_seconds must not be negative")
        if self.min_size < 0:
            raise ValueError("min_size must not be negative")
        if self.jobs < 1:
            raise ValueError("jobs must be at least 1")
