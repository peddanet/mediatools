"""Planning phase of a shrink run: which videos are encoded, in which order."""
from __future__ import annotations

import os
from collections.abc import Callable

from mediatools.core.durationcache import DurationCache
from mediatools.core.mediatypes import MediaTypes
from mediatools.core.probe import MediaProbe
from mediatools.core.walker import TreeWalker
from mediatools.shrink.database import ShrinkDatabase
from mediatools.shrink.journal import ReplaceJournal
from mediatools.shrink.options import ShrinkOptions
from mediatools.shrink.shrinkstate import ShrinkState


class ShrinkPlanner:
    """Walks the tree and lists the videos a shrink run encodes.

    Per directory: finish replacements interrupted by a crash
    (:class:`ReplaceJournal`), note leftover ``newShrinkedFiles/`` folders
    of earlier aborted runs, and collect every video that is not yet
    recorded in its directory's database. The database always has
    precedence: a recorded file counts as processed, whatever its codec.
    A video recorded as failed (``FAILED`` or ``BROKEN``) with unchanged
    size is left out unless ``retry_failed`` is set. Then optionally drop
    videos shorter than ``min_seconds``, set aside videos that are already
    HEVC and sort the rest (``largest`` first, ``smallest`` first or walk
    order ``path``).

    Results go into the run's shared ``stats`` and ``found`` dictionaries
    (see :class:`Shrinker`).

    Attributes:
        root: Absolute start directory.
        log: Logger.
        opts: Run settings.
        probe: Probe for codec and duration checks.
        state: Run helpers; ``durations`` and ``codecs`` are used.
        database_of: Returns the database of a directory.
        journal: Completes interrupted replacements.
        stats: Shared counters ``skipped``, ``short``, ``recovered``,
            ``failed_before``.
        found: Shared lists ``leftovers``, ``hevc`` and ``failed_before``.
    """

    CHECKPOINT = 500

    def __init__(self, root: str, logger, opts: ShrinkOptions, probe: MediaProbe,
                 state: ShrinkState, database_of: Callable[[str], ShrinkDatabase],
                 journal: ReplaceJournal, results: tuple[dict, dict]) -> None:
        """Bind the planner to a run.

        Args:
            root: Absolute start directory.
            logger: :class:`logging.Logger`.
            opts: Run settings.
            probe: Probe for codec and duration checks.
            state: Run helpers (caches).
            database_of: Returns the database of a directory.
            journal: Completes interrupted replacements.
            results: The run's ``(stats, found)`` dictionaries.
        """
        self.root = root
        self.log = logger
        self.opts = opts
        self.probe = probe
        self.state = state
        self.database_of = database_of
        self.journal = journal
        self.stats, self.found = results

    def plan(self, stopped: Callable[[], bool]) -> list[tuple[str, int]]:
        """Recover interrupted replacements and list the videos to encode.

        Args:
            stopped: Returns True once the run should stop.

        Returns:
            ``(path, size)`` pairs in processing order.
        """
        todo: list[tuple[str, int]] = []
        for dirpath, _dirs, files in TreeWalker().walk(self.root):
            if not self.opts.keep_copies:
                self.stats["recovered"] += self.journal.recover(dirpath, self.database_of)
                self._note_leftover(dirpath)
            for name in files:
                entry = self._candidate(os.path.join(dirpath, name))
                if entry is not None:
                    todo.append(entry)
            if stopped():
                break
        if self.opts.min_seconds > 0:
            todo = self._drop_short(todo)
        todo = self._set_aside_hevc(todo)
        if self.opts.order == "largest":
            todo.sort(key=lambda item: item[1], reverse=True)
        elif self.opts.order == "smallest":
            todo.sort(key=lambda item: item[1])
        return todo

    def _candidate(self, path: str) -> tuple[str, int] | None:
        """Decide whether one file of the walk is a candidate.

        Args:
            path: File in the tree.

        Returns:
            ``(path, size)`` if it is to be encoded, else None (other
            extension, link, placeholder, recorded or recorded as failed).
        """
        if MediaTypes.extension(os.path.basename(path)) not in self.opts.extensions:
            return None
        if os.path.islink(path) or not os.path.isfile(path):
            return None
        size = os.path.getsize(path)
        if size == 0:
            return None                              # triage placeholder
        database = self.database_of(os.path.dirname(path))
        if database.is_done(path):
            self.stats["skipped"] += 1
            return None
        if not self.opts.retry_failed and database.failure(path) is not None:
            self.stats["failed_before"] += 1
            self.found["failed_before"].append(path)
            return None
        return path, size

    def _drop_short(self, todo: list[tuple[str, int]]) -> list[tuple[str, int]]:
        """Remove videos shorter than ``min_seconds``.

        Args:
            todo: Candidates.

        Returns:
            Candidates with a known duration of at least ``min_seconds``
            (unknown durations are kept).
        """
        kept = []
        for number, (path, size) in enumerate(todo, 1):
            if number % self.CHECKPOINT == 0 and number < len(todo):
                self._checkpoint("durations", self.state["durations"], number, len(todo))
            duration = self._duration(path)
            if duration is not None and duration < self.opts.min_seconds:
                self.stats["short"] += 1
            else:
                kept.append((path, size))
        self._checkpoint("durations", self.state["durations"], len(todo), len(todo))
        return kept

    def _set_aside_hevc(self, todo: list[tuple[str, int]]) -> list[tuple[str, int]]:
        """Move videos that are already HEVC out of the encode list.

        Args:
            todo: Candidates.

        Returns:
            Candidates that need encoding; the others go to
            ``found["hevc"]``.
        """
        rest = []
        for number, (path, size) in enumerate(todo, 1):
            if number % self.CHECKPOINT == 0 and number < len(todo):
                self._checkpoint("codecs", self.state["codecs"], number, len(todo))
            if self._codec(path) == "hevc":
                self.found["hevc"].append((path, size))
            else:
                rest.append((path, size))
        self._checkpoint("codecs", self.state["codecs"], len(todo), len(todo))
        return rest

    def _checkpoint(self, what: str, cache: DurationCache | None,
                    number: int, total: int) -> None:
        """Report planning progress and store newly probed values.

        Saving every :attr:`CHECKPOINT` files keeps the probes of a long
        first check even if the run is interrupted during planning.

        Args:
            what: ``durations`` or ``codecs``.
            cache: The cache in use, or None.
            number: Files checked so far.
            total: Files to check.
        """
        if total == 0:
            return
        if cache is None:
            self.log.info("checking %s: %i of %i", what, number, total)
            return
        self.log.info("checking %s: %i of %i (%i from cache)", what, number, total, cache.hits)
        if cache.added:
            cache.save()

    def _codec(self, path: str) -> str | None:
        """Return a video's codec, from the cache if possible.

        Args:
            path: Video file.

        Returns:
            Codec name, or None if unknown.
        """
        if self.state["codecs"] is None:
            return self.probe.video_codec(path)
        st = os.stat(path)
        codec = self.state["codecs"].get(path, st)
        if codec is None:
            codec = self.probe.video_codec(path)
            if codec is not None:
                self.state["codecs"].put(path, st, codec)
        return codec

    def _duration(self, path: str) -> float | None:
        """Return a video's duration, from the cache if possible.

        Args:
            path: Video file.

        Returns:
            Seconds, or None if unknown.
        """
        if self.state["durations"] is None:
            return self.probe.duration(path)
        st = os.stat(path)
        duration = self.state["durations"].get(path, st)
        if duration is None:
            duration = self.probe.duration(path)
            if duration is not None:
                self.state["durations"].put(path, st, duration)
        return duration

    def _note_leftover(self, dirpath: str) -> None:
        """Remember a leftover work folder for the plan summary.

        Args:
            dirpath: Directory whose ``newShrinkedFiles`` is checked.
        """
        tmp = os.path.join(dirpath, MediaTypes.SHRINK_TMP_DIR)
        if not os.path.isdir(tmp):
            return
        files = nbytes = 0
        for base, _dirs, names in os.walk(tmp):
            for name in names:
                files += 1
                try:
                    nbytes += os.path.getsize(os.path.join(base, name))
                except OSError:
                    pass
        self.found["leftovers"].append((tmp, files, nbytes))
