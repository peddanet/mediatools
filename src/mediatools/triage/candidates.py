"""Selecting the videos to review."""
from __future__ import annotations

import os
from typing import Iterator

from mediatools.core.probe import MediaProbe
from mediatools.core.walker import TreeWalker
from mediatools.triage.durationcache import DurationCache
from mediatools.triage.keptlist import KeptList


class CandidateFinder:
    """Yields videos whose duration lies in ``[min_s, max_s]``.

    Iterating is lazy (a generator); :meth:`collect` runs the whole search
    first so that the number of candidates is known before the review
    starts. ``_flat`` and ``newShrinkedFiles`` are never entered and empty
    placeholder files are skipped, hence every video is offered once under
    its original name. Durations are taken from a :class:`DurationCache`
    when one is given, so repeated scans are fast.

    Attributes:
        root: Start directory.
        extensions: Accepted extensions.
        min_s: Lower duration threshold in seconds.
        max_s: Upper duration threshold in seconds.
        kept: Kept list or None (then nothing is filtered).
        probe: Probe for the durations.
        log: Optional logger.
        cache: Optional duration cache.
        scanned: Number of videos looked at so far.
    """

    def __init__(self, root: str, extensions: frozenset, min_s: float, max_s: float,
                 kept: KeptList | None = None, probe: MediaProbe | None = None, logger=None,
                 cache: DurationCache | None = None):
        """Configure the search.

        Args:
            root: Start directory.
            extensions: Accepted extensions.
            min_s: Lower duration threshold in seconds.
            max_s: Upper duration threshold in seconds.
            kept: Files on this list are skipped; None disables the filter.
            probe: Probe; a new one if None.
            logger: Optional :class:`logging.Logger`.
            cache: Duration cache; None probes every file.
        """
        self.cache = cache
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
            duration = self._duration(path)
            if duration is None:
                if self.log:
                    self.log.warning("duration unknown, ignored: %s", path)
                continue
            if self.min_s <= duration <= self.max_s:
                yield path, duration

    def _duration(self, path: str) -> float | None:
        """Return the duration, from the cache if possible.

        Args:
            path: Video file.

        Returns:
            Duration in seconds, or None if ffprobe cannot tell.
        """
        if self.cache is None:
            return self.probe.duration(path)
        st = os.stat(path)
        duration = self.cache.get(path, st)
        if duration is None:
            duration = self.probe.duration(path)
            if duration is not None:
                self.cache.put(path, st, duration)
        return duration

    def collect(self) -> list[tuple[str, float]]:
        """Run the complete search before the review starts.

        Returns:
            All matching ``(path, duration)`` pairs in walk order; the cache
            (if any) is saved afterwards.
        """
        result = list(self)
        if self.cache is not None:
            self.cache.save()
        return result
