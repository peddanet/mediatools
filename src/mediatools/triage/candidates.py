"""Selecting the videos to review."""
from __future__ import annotations

from typing import Iterator

from mediatools.core.probe import MediaProbe
from mediatools.core.walker import TreeWalker
from mediatools.triage.keptlist import KeptList


class CandidateFinder:
    """Yields videos whose duration lies in ``[min_s, max_s]``.

    The search is lazy (a generator), so the first batch can be shown before
    the whole tree has been probed. ``_flat`` and ``newShrinkedFiles`` are
    never entered, hence every video is offered once under its original
    name.

    Attributes:
        root: Start directory.
        extensions: Accepted extensions.
        min_s: Lower duration threshold in seconds.
        max_s: Upper duration threshold in seconds.
        kept: Kept list or None (then nothing is filtered).
        probe: Probe for the durations.
        log: Optional logger.
        scanned: Number of videos looked at so far.
    """

    def __init__(self, root: str, extensions: frozenset, min_s: float, max_s: float,
                 kept: KeptList | None = None, probe: MediaProbe | None = None, logger=None):
        """Configure the search.

        Args:
            root: Start directory.
            extensions: Accepted extensions.
            min_s: Lower duration threshold in seconds.
            max_s: Upper duration threshold in seconds.
            kept: Files on this list are skipped; None disables the filter.
            probe: Probe; a new one if None.
            logger: Optional :class:`logging.Logger`.
        """
        self.root = root
        self.extensions = extensions
        self.min_s = min_s
        self.max_s = max_s
        self.kept = kept
        self.probe = probe or MediaProbe()
        self.log = logger
        self.scanned = 0

    def __iter__(self) -> Iterator[tuple[str, float]]:
        """Walk the tree and probe each video.

        Yields:
            ``(path, duration_seconds)`` of every matching video.
        """
        for path in TreeWalker().media_files(self.root, self.extensions):
            self.scanned += 1
            if self.scanned % 100 == 0:
                print(f"  ... {self.scanned} videos scanned", flush=True)
            if self.kept is not None and self.kept.contains(path):
                continue
            duration = self.probe.duration(path)
            if duration is None:
                if self.log:
                    self.log.warning("duration unknown, ignored: %s", path)
                continue
            if self.min_s <= duration <= self.max_s:
                yield path, duration
