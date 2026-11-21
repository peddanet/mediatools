"""Concrete shrink jobs derived from an estimate, best first."""
from __future__ import annotations

from dataclasses import dataclass

from mediatools.shrink.potential import PotentialGroup, ShrinkPotential


@dataclass
class SuggestedJob:
    """One proposed ``shrink`` run.

    Attributes:
        level: Level number (1 = the given folder of the estimate).
        min_seconds: Value for ``--min-seconds``.
        files: Videos it is expected to encode.
        size: Their size in bytes.
        saved: Bytes expected to be freed.
        seconds: Expected wall-clock seconds (cautious).
        seconds_low: Expected wall-clock seconds (optimistic).
    """

    level: int
    min_seconds: int
    files: int = 0
    size: int = 0
    saved: float = 0.0
    seconds: float = 0.0
    seconds_low: float = 0.0

    @property
    def per_hour(self) -> float:
        """Bytes freed per hour (cautious)."""
        return self.saved / max(1.0, self.seconds) * 3600.0


class JobSuggester:
    """Turns the groups of an estimate into an ordered list of shrink jobs.

    ``shrink`` can filter by folder and playing time (``--min-seconds``),
    not by bit rate, so a job is "folder of level ``k`` with
    ``--min-seconds s``". It takes every group not taken yet whose level is
    ``k`` or inside it and whose length band starts at ``s`` or later (a
    folder contains the levels inside it; videos already shrunk are skipped
    by ``shrink`` anyway). Videos of unknown length count only for
    ``--min-seconds 0``.

    The list is built greedily: of all combinations of level and
    ``--min-seconds``, the one that frees the most per hour (cautious time)
    comes first; its groups are taken; the rest is ranked again - until the
    limit is reached or no job would free at least :attr:`MIN_BYTES`. Since
    the jobs run in this order, each one finds the earlier ones done.

    Attributes:
        potential: The estimate.
    """

    MIN_BYTES = 1024 ** 3

    def __init__(self, potential: ShrinkPotential) -> None:
        """Bind the suggester to an estimate.

        Args:
            potential: Grouped videos.
        """
        self.potential = potential

    def low(self, group: PotentialGroup) -> float:
        """Return the start of a group's length band (0 for unknown length).

        Args:
            group: The group.

        Returns:
            Seconds.
        """
        return 0.0 if group.band == ShrinkPotential.UNKNOWN else \
            next(low for low, _high, label in ShrinkPotential.BANDS if label == group.band)

    @staticmethod
    def level(group: PotentialGroup) -> int:
        """Return a group's level number.

        Args:
            group: The group.

        Returns:
            1 for ``L1`` and so on.
        """
        return int(group.level[1:]) if group.level[1:].isdigit() else 1

    def suggest(self, limit: int) -> list[SuggestedJob]:
        """Return up to ``limit`` jobs, best first.

        Args:
            limit: Maximum number of jobs.

        Returns:
            The jobs with what each is expected to take in.
        """
        left = list(self.potential.groups.values())
        levels = sorted({self.level(group) for group in left})
        lows = sorted({int(low) for low, _high, _label in ShrinkPotential.BANDS}, reverse=True)
        jobs: list[SuggestedJob] = []
        while left and len(jobs) < limit:
            best: SuggestedJob | None = None
            best_groups: list[PotentialGroup] = []
            for level in levels:
                for low in lows:
                    taken = [group for group in left
                             if self.level(group) <= level and self.low(group) >= low]
                    job = self.total(level, low, taken)
                    if job.saved >= self.MIN_BYTES and (best is None
                                                        or job.per_hour > best.per_hour):
                        best, best_groups = job, taken
            if best is None:
                break
            jobs.append(best)
            left = [group for group in left if group not in best_groups]
        return jobs

    @staticmethod
    def total(level: int, low: int, groups: list[PotentialGroup]) -> SuggestedJob:
        """Sum up groups into one job.

        Args:
            level: Level number.
            low: ``--min-seconds``.
            groups: Groups it takes.

        Returns:
            The job.
        """
        return SuggestedJob(level, low, sum(g.files for g in groups),
                            sum(g.size for g in groups), sum(g.saved for g in groups),
                            sum(g.seconds for g in groups), sum(g.seconds_low for g in groups))
