"""Estimate of the space shrinking could still free, grouped by level, length and bit rate."""
from __future__ import annotations

import math
from dataclasses import dataclass

from mediatools.shrink.ledgerrow import LedgerRow
from mediatools.shrink.timemodel import TimeModel


@dataclass
class PotentialGroup:
    """Videos of one level, one length band and one bit-rate class.

    Attributes:
        band: Length band, e.g. ``5-10 min``.
        quality: Bit-rate class, e.g. ``2-4 Mbit/s``.
        files: Number of videos.
        size: Their size in bytes.
        ratio: Expected output share (output bytes per source byte).
        level: Level label (``L1`` = the given folder, ``L2`` its parent ...).
        length: Their total playing time in seconds (guessed from the size
            where it is unknown).
        model: Time model, or None to count one wall second per second of
            playing time.
    """

    band: str
    quality: str
    files: int = 0
    size: int = 0
    ratio: float = 0.2
    level: str = "L1"
    length: float = 0.0
    model: TimeModel | None = None

    @property
    def saved(self) -> float:
        """Bytes expected to be freed."""
        return self.size * (1.0 - self.ratio)

    def times(self) -> tuple[float, float]:
        """Expected wall-clock seconds, ``(cautious, optimistic)``.

        Returns:
            Both variants; equal when the fixed part is measured.
        """
        if self.model is None:
            return self.length, self.length
        return self.model.seconds(self.quality, self.files, self.length)

    @property
    def seconds(self) -> float:
        """Expected encoding time in wall-clock seconds (cautious variant)."""
        return self.times()[0]

    @property
    def seconds_low(self) -> float:
        """Expected encoding time in wall-clock seconds (optimistic variant)."""
        return self.times()[1]

    @property
    def per_hour(self) -> float:
        """Bytes freed per hour of encoding (cautious variant)."""
        return self.saved / max(1.0, self.seconds) * 3600.0


class ShrinkPotential:
    """Groups the videos still to shrink and estimates savings and time.

    The estimate uses what was measured so far, not a model of the encoder:

    * **output share** per bit-rate class: sum of output bytes over sum of
      source bytes of the shrunk videos in the ledger whose source had that
      bit rate (size x 8 / playing time). A class with fewer than
      :attr:`MIN_SAMPLES` measured videos uses the overall share.
    * **time**: :class:`TimeModel` - a fixed part per video plus a part that
      grows with the playing time, per bit-rate class, fitted on the ledger
      and scaled to the measured wall-clock speed of the whole run
      (``ThroughputLog``, parallel encodes included). While the fixed part is
      only extrapolated, every time is a range (optimistic to cautious).

    Videos are grouped by level (``L1`` the given folder, ``L2`` what its
    parent adds, ...), playing time (the bands of ``--min-seconds``) and bit
    rate (a rough measure of quality: a high bit rate has the most to
    lose). The groups are ranked by space freed per hour of encoding in the
    cautious variant, so the first rows are the best next step.

    Attributes:
        ratios: Output share per bit-rate class.
        overall_ratio: Output share over all measured videos.
        overall_rate: Measured wall-clock speed in source bytes per second.
        samples: Measured videos per bit-rate class.
        model: The time model.
        groups: Groups by ``(level, band, quality)``.
    """

    BANDS = ((0, 60, "< 1 min"), (60, 120, "1-2 min"), (120, 300, "2-5 min"),
             (300, 600, "5-10 min"), (600, 1800, "10-30 min"), (1800, math.inf, ">= 30 min"))
    QUALITIES = ((0, 1, "< 1 Mbit/s"), (1, 2, "1-2 Mbit/s"), (2, 4, "2-4 Mbit/s"),
                 (4, 8, "4-8 Mbit/s"), (8, math.inf, ">= 8 Mbit/s"))
    UNKNOWN = "unknown"
    MIN_SAMPLES = TimeModel.MIN_SAMPLES
    DEFAULT_RATIO = 0.2
    DEFAULT_RATE = 2.0e6

    def __init__(self, rows: list[LedgerRow], wall_rate: float | None,
                 overhead: float | None = None) -> None:
        """Derive output share and the time model from the ledger.

        Args:
            rows: Ledger entries.
            wall_rate: Measured wall-clock speed of shrinking (source bytes
                per second), or None.
            overhead: Fixed wall-clock seconds per video to use instead of
                the fitted value, or None to fit it.
        """
        used = [row for row in rows if self.usable(row)]
        sums: dict[str, list[float]] = {}
        for row in used:
            entry = sums.setdefault(self.quality_of(row.source, row.length), [0.0, 0.0, 0.0])
            entry[0] += row.source
            entry[1] += row.result or 0
            entry[2] += 1
        src = sum(row.source for row in used)
        took = sum(row.took for row in used)
        self.overall_ratio = (sum(entry[1] for entry in sums.values()) / src if src
                              else self.DEFAULT_RATIO)
        file_rate = src / took if took else 0.0
        scale = wall_rate / file_rate if wall_rate and file_rate else 1.0
        self.overall_rate = wall_rate or (file_rate or self.DEFAULT_RATE)
        self.samples = {quality: int(entry[2]) for quality, entry in sums.items()}
        self.ratios = {quality: entry[1] / entry[0] if entry[2] >= self.MIN_SAMPLES
                       else self.overall_ratio for quality, entry in sums.items()}
        self.model = TimeModel(used, self.quality_of, scale, overhead)
        self.groups: dict[tuple[str, str, str], PotentialGroup] = {}

    @staticmethod
    def usable(row: LedgerRow) -> bool:
        """Tell whether a ledger entry is a measured, finished encode.

        Args:
            row: Ledger entry.

        Returns:
            True for ``shrunk``/``copy`` rows with sizes, time and length.
        """
        return (row.status in ("shrunk", "copy") and bool(row.result) and bool(row.length)
                and row.took > 0 and row.source > 0)

    @classmethod
    def band_of(cls, seconds: float | None) -> str:
        """Return the length band of a playing time.

        Args:
            seconds: Playing time, or None.

        Returns:
            Band label, or ``unknown``.
        """
        if seconds is None:
            return cls.UNKNOWN
        return next(label for low, high, label in cls.BANDS if low <= seconds < high)

    @classmethod
    def quality_of(cls, size: int, seconds: float | None) -> str:
        """Return the bit-rate class of a video.

        Args:
            size: Size in bytes.
            seconds: Playing time, or None.

        Returns:
            Class label, or ``unknown`` without a playing time.
        """
        if not seconds or seconds <= 0:
            return cls.UNKNOWN
        mbit = size * 8 / seconds / 1e6
        return next(label for low, high, label in cls.QUALITIES if low <= mbit < high)

    def add(self, size: int, seconds: float | None, level: str = "L1") -> None:
        """Count one video still to shrink.

        Args:
            size: Its size in bytes.
            seconds: Its playing time, or None if unknown.
            level: Level label of the folder it belongs to.
        """
        quality = self.quality_of(size, seconds)
        key = (level, self.band_of(seconds), quality)
        group = self.groups.get(key)
        if group is None:
            group = PotentialGroup(key[1], quality,
                                   ratio=self.ratios.get(quality, self.overall_ratio),
                                   level=level, model=self.model)
            self.groups[key] = group
        group.files += 1
        group.size += size
        group.length += seconds if seconds and seconds > 0 else size / self.model.byte_rate

    def ranked(self) -> list[PotentialGroup]:
        """Return the groups, the most space per hour first.

        Returns:
            Groups sorted by :attr:`PotentialGroup.per_hour`, descending.
        """
        return sorted(self.groups.values(), key=lambda group: group.per_hour, reverse=True)

    def levels(self) -> list[str]:
        """Return the level labels that have videos.

        Returns:
            Labels in order (``L1`` first).
        """
        return sorted({group.level for group in self.groups.values()},
                      key=lambda label: int(label[1:]) if label[1:].isdigit() else 0)

    def thresholds(self, level: str | None = None
                   ) -> list[tuple[str, int, int, float, float, float]]:
        """Sum up what each ``--min-seconds`` value would take in.

        Args:
            level: Only the videos of this level, or None for all.

        Returns:
            ``(min-seconds label, files, bytes, bytes freed, seconds,
            seconds optimistic)`` per threshold, from the longest videos only
            down to all videos.
        """
        result = []
        for low, _high, _label in reversed(self.BANDS):
            taken = [group for group in self.groups.values()
                     if group.band != self.UNKNOWN and self._low(group.band) >= low
                     and level in (None, group.level)]
            result.append((f"--min-seconds {low:g}", sum(g.files for g in taken),
                           sum(g.size for g in taken), sum(g.saved for g in taken),
                           sum(g.seconds for g in taken), sum(g.seconds_low for g in taken)))
        return result

    def _low(self, band: str) -> float:
        """Return the lower bound of a band label.

        Args:
            band: Band label.

        Returns:
            Seconds.
        """
        return next(low for low, _high, label in self.BANDS if label == band)

    def table(self, limit: int | None = None) -> list[str]:
        """Format the ranked groups with running totals.

        Args:
            limit: Show at most this many groups (the totals still run over
                the rows shown), or None for all.

        Returns:
            Text lines, header first.
        """
        gib = 1024 ** 3
        low = self.model.uncertain
        lines = [f"{'level':<5} {'length':<10} {'bit rate':<12} {'files':>6} {'GiB':>8} "
                 f"{'freed GiB':>10} {'hours':>7} " + (f"{'h min':>6} " if low else "")
                 + f"{'GiB/h':>6} {'sum freed':>10} {'sum hours':>9}"]
        freed = hours = 0.0
        for group in self.ranked()[:limit]:
            freed += group.saved
            hours += group.seconds / 3600
            lines.append(f"{group.level:<5} {group.band:<10} {group.quality:<12} {group.files:>6} "
                         f"{group.size / gib:>8.1f} {group.saved / gib:>10.1f} "
                         f"{group.seconds / 3600:>7.1f} "
                         + (f"{group.seconds_low / 3600:>6.1f} " if low else "")
                         + f"{group.per_hour / gib:>6.1f} {freed / gib:>10.1f} {hours:>9.1f}")
        return lines
