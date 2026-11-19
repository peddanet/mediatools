"""Estimate of the space shrinking could still free, grouped by length and bit rate."""
from __future__ import annotations

import math
from dataclasses import dataclass

from mediatools.shrink.ledgerrow import LedgerRow


@dataclass
class PotentialGroup:
    """Videos of one length band and one bit-rate class.

    Attributes:
        band: Length band, e.g. ``5-10 min``.
        quality: Bit-rate class, e.g. ``2-4 Mbit/s``.
        files: Number of videos.
        size: Their size in bytes.
        ratio: Expected output share (output bytes per source byte).
        rate: Expected speed in source bytes per wall-clock second.
    """

    band: str
    quality: str
    files: int = 0
    size: int = 0
    ratio: float = 0.2
    rate: float = 1.0

    @property
    def saved(self) -> float:
        """Bytes expected to be freed."""
        return self.size * (1.0 - self.ratio)

    @property
    def seconds(self) -> float:
        """Expected encoding time in wall-clock seconds."""
        return self.size / self.rate

    @property
    def per_hour(self) -> float:
        """Bytes freed per hour of encoding."""
        return self.saved / max(1.0, self.seconds) * 3600.0


class ShrinkPotential:
    """Groups the videos still to shrink and estimates savings and time.

    The estimate uses what was measured so far, not a model of the encoder:

    * **output share** per bit-rate class: sum of output bytes over sum of
      source bytes of the shrunk videos in the ledger whose source had that
      bit rate (size x 8 / playing time). A class with fewer than
      :attr:`MIN_SAMPLES` measured videos uses the overall share.
    * **speed** per bit-rate class: source bytes per second spent on the
      file (ledger), scaled to the measured wall-clock speed of the whole
      run (``ThroughputLog``), which includes parallel encodes.

    Videos are grouped by playing time (the bands of ``--min-seconds``) and
    by bit rate (a rough measure of quality: a high bit rate has the most to
    lose). The groups are ranked by space freed per hour of encoding, so
    the first rows are the best next step.

    Attributes:
        ratios: Output share per bit-rate class.
        rates: Speed per bit-rate class (source bytes per wall second).
        overall_ratio: Output share over all measured videos.
        samples: Measured videos per bit-rate class.
        groups: Groups by ``(band, quality)``.
    """

    BANDS = ((0, 60, "< 1 min"), (60, 120, "1-2 min"), (120, 300, "2-5 min"),
             (300, 600, "5-10 min"), (600, 1800, "10-30 min"), (1800, math.inf, ">= 30 min"))
    QUALITIES = ((0, 1, "< 1 Mbit/s"), (1, 2, "1-2 Mbit/s"), (2, 4, "2-4 Mbit/s"),
                 (4, 8, "4-8 Mbit/s"), (8, math.inf, ">= 8 Mbit/s"))
    UNKNOWN = "unknown"
    MIN_SAMPLES = 20
    DEFAULT_RATIO = 0.2
    DEFAULT_RATE = 2.0e6

    def __init__(self, rows: list[LedgerRow], wall_rate: float | None) -> None:
        """Derive output share and speed per bit-rate class from the ledger.

        Args:
            rows: Ledger entries.
            wall_rate: Measured wall-clock speed of shrinking (source bytes
                per second), or None.
        """
        sums: dict[str, list[float]] = {}
        for row in rows:
            if row.status not in ("shrunk", "copy") or not row.result or not row.length \
                    or row.took <= 0 or row.source <= 0:
                continue
            quality = self.quality_of(row.source, row.length)
            entry = sums.setdefault(quality, [0.0, 0.0, 0.0, 0.0])
            entry[0] += row.source
            entry[1] += row.result
            entry[2] += row.took
            entry[3] += 1
        src = sum(entry[0] for entry in sums.values())
        out = sum(entry[1] for entry in sums.values())
        took = sum(entry[2] for entry in sums.values())
        self.overall_ratio = out / src if src else self.DEFAULT_RATIO
        file_rate = src / took if took else 0.0
        scale = wall_rate / file_rate if wall_rate and file_rate else 1.0
        overall_rate = wall_rate or (file_rate or self.DEFAULT_RATE)
        self.samples = {quality: int(entry[3]) for quality, entry in sums.items()}
        self.ratios: dict[str, float] = {}
        self.rates: dict[str, float] = {}
        for quality, entry in sums.items():
            enough = entry[3] >= self.MIN_SAMPLES
            self.ratios[quality] = entry[1] / entry[0] if enough else self.overall_ratio
            self.rates[quality] = entry[0] / entry[2] * scale if enough else overall_rate
        self.overall_rate = overall_rate
        self.groups: dict[tuple[str, str], PotentialGroup] = {}

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

    def add(self, size: int, seconds: float | None) -> None:
        """Count one video still to shrink.

        Args:
            size: Its size in bytes.
            seconds: Its playing time, or None if unknown.
        """
        quality = self.quality_of(size, seconds)
        key = (self.band_of(seconds), quality)
        group = self.groups.get(key)
        if group is None:
            group = PotentialGroup(key[0], quality,
                                   ratio=self.ratios.get(quality, self.overall_ratio),
                                   rate=self.rates.get(quality, self.overall_rate))
            self.groups[key] = group
        group.files += 1
        group.size += size

    def ranked(self) -> list[PotentialGroup]:
        """Return the groups, the most space per hour first.

        Returns:
            Groups sorted by :attr:`PotentialGroup.per_hour`, descending.
        """
        return sorted(self.groups.values(), key=lambda group: group.per_hour, reverse=True)

    def thresholds(self) -> list[tuple[str, int, int, float, float]]:
        """Sum up what each ``--min-seconds`` value would take in.

        Returns:
            ``(min-seconds label, files, bytes, bytes freed, seconds)`` per
            threshold, from the longest videos only down to all videos.
        """
        result = []
        for low, _high, _label in reversed(self.BANDS):
            taken = [group for group in self.groups.values()
                     if group.band != self.UNKNOWN and self._low(group.band) >= low]
            result.append((f"--min-seconds {low:g}", sum(g.files for g in taken),
                           sum(g.size for g in taken), sum(g.saved for g in taken),
                           sum(g.seconds for g in taken)))
        return result

    def _low(self, band: str) -> float:
        """Return the lower bound of a band label.

        Args:
            band: Band label.

        Returns:
            Seconds.
        """
        return next(low for low, _high, label in self.BANDS if label == band)

    def table(self) -> list[str]:
        """Format the ranked groups with running totals.

        Returns:
            Text lines, header first.
        """
        gib = 1024 ** 3
        lines = [f"{'length':<10} {'bit rate':<12} {'files':>6} {'GiB':>8} {'freed GiB':>10} "
                 f"{'hours':>7} {'GiB/h':>6} {'sum freed':>10} {'sum hours':>9}"]
        freed = hours = 0.0
        for group in self.ranked():
            freed += group.saved
            hours += group.seconds / 3600
            lines.append(f"{group.band:<10} {group.quality:<12} {group.files:>6} "
                         f"{group.size / gib:>8.1f} {group.saved / gib:>10.1f} "
                         f"{group.seconds / 3600:>7.1f} {group.per_hour / gib:>6.1f} "
                         f"{freed / gib:>10.1f} {hours:>9.1f}")
        return lines
