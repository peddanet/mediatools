"""Estimate of the space shrinking could still free, grouped by level, length and bit rate."""
from __future__ import annotations

import math
from dataclasses import dataclass

from mediatools.shrink.ledgerrow import LedgerRow


@dataclass
class PotentialGroup:
    """Videos of one level, one length band and one bit-rate class.

    Attributes:
        band: Length band, e.g. ``5-10 min``.
        quality: Bit-rate class, e.g. ``2-4 Mbit/s``.
        files: Number of videos.
        size: Their size in bytes.
        ratio: Expected output share (output bytes per source byte).
        rate: Expected speed in source bytes per wall-clock second.
        level: Level label (``L1`` = the given folder, ``L2`` its parent ...).
        overhead: Expected fixed wall-clock seconds per video, in addition to the
            time that grows with the size (start, checks, validation).
    """

    band: str
    quality: str
    files: int = 0
    size: int = 0
    ratio: float = 0.2
    rate: float = 1.0
    level: str = "L1"
    overhead: float = 0.0

    @property
    def saved(self) -> float:
        """Bytes expected to be freed."""
        return self.size * (1.0 - self.ratio)

    @property
    def seconds(self) -> float:
        """Expected encoding time in wall-clock seconds (fixed part included)."""
        return self.size / self.rate + self.files * self.overhead

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

    * **fixed time per video**: the ledger time of a video is fitted as
      ``a + b x size`` (least squares); ``a`` is the part every video costs
      regardless of its size - start of ffmpeg, trial decode, validation,
      flushing. It matters for many short videos, where a pure
      size/speed estimate is too optimistic. It is scaled to wall-clock time
      like the speed and can be set by the caller instead (``overhead``).

    Videos are grouped by level (``L1`` the given folder, ``L2`` what its
    parent adds, ...), playing time (the bands of ``--min-seconds``) and bit
    rate (a rough measure of quality: a high bit rate has the most to
    lose). The groups are ranked by space freed per hour of encoding, so
    the first rows are the best next step.

    Attributes:
        ratios: Output share per bit-rate class.
        rates: Speed per bit-rate class (source bytes per wall second).
        overall_ratio: Output share over all measured videos.
        samples: Measured videos per bit-rate class.
        overhead: Fixed wall-clock seconds per video used for the estimate.
        overhead_basis: How :attr:`overhead` was found (for the report).
        groups: Groups by ``(level, band, quality)``.
    """

    BANDS = ((0, 60, "< 1 min"), (60, 120, "1-2 min"), (120, 300, "2-5 min"),
             (300, 600, "5-10 min"), (600, 1800, "10-30 min"), (1800, math.inf, ">= 30 min"))
    QUALITIES = ((0, 1, "< 1 Mbit/s"), (1, 2, "1-2 Mbit/s"), (2, 4, "2-4 Mbit/s"),
                 (4, 8, "4-8 Mbit/s"), (8, math.inf, ">= 8 Mbit/s"))
    UNKNOWN = "unknown"
    MIN_SAMPLES = 20
    DEFAULT_RATIO = 0.2
    DEFAULT_RATE = 2.0e6

    SHORT = 300.0

    def __init__(self, rows: list[LedgerRow], wall_rate: float | None,
                 overhead: float | None = None) -> None:
        """Derive output share, speed and fixed time per video from the ledger.

        Args:
            rows: Ledger entries.
            wall_rate: Measured wall-clock speed of shrinking (source bytes
                per second), or None.
            overhead: Fixed wall-clock seconds per video to use instead of
                the fitted value, or None to fit it.
        """
        sums: dict[str, list[float]] = {}
        used = [row for row in rows if self.usable(row)]
        for row in used:
            quality = self.quality_of(row.source, row.length)
            entry = sums.setdefault(quality, [0.0, 0.0, 0.0, 0.0])
            entry[0] += row.source
            entry[1] += row.result or 0
            entry[2] += row.took
            entry[3] += 1
        src = sum(entry[0] for entry in sums.values())
        out = sum(entry[1] for entry in sums.values())
        took = sum(entry[2] for entry in sums.values())
        self.overall_ratio = out / src if src else self.DEFAULT_RATIO
        file_rate = src / took if took else 0.0
        scale = wall_rate / file_rate if wall_rate and file_rate else 1.0
        overall_rate = wall_rate or (file_rate or self.DEFAULT_RATE)
        self.overhead, self.overhead_basis = self._overhead(used, scale, overhead)
        fixed = self.overhead * scale                     # per video, in per-file seconds
        if took and fixed:
            overall_rate = src / self.variable(took, len(used), fixed) * scale
        self.samples = {quality: int(entry[3]) for quality, entry in sums.items()}
        self.ratios: dict[str, float] = {}
        self.rates: dict[str, float] = {}
        for quality, entry in sums.items():
            enough = entry[3] >= self.MIN_SAMPLES
            self.ratios[quality] = entry[1] / entry[0] if enough else self.overall_ratio
            self.rates[quality] = (entry[0] / self.variable(entry[2], entry[3], fixed) * scale
                                   if enough else overall_rate)
        self.overall_rate = overall_rate
        self.groups: dict[tuple[str, str, str], PotentialGroup] = {}

    @staticmethod
    def variable(took: float, count: float, fixed: float) -> float:
        """Return the part of the measured time that grows with the size.

        Args:
            took: Measured seconds of ``count`` videos.
            count: Number of videos.
            fixed: Fixed seconds per video.

        Returns:
            ``took - count x fixed``, but at least a tenth of ``took`` (a
            fixed time set too high must not make the speed infinite).
        """
        return max(took - count * fixed, took / 10.0)

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

    @staticmethod
    def fit(rows: list[LedgerRow]) -> tuple[float, float] | None:
        """Fit the time of a video as ``a + b x size`` by least squares.

        Args:
            rows: Usable ledger entries.

        Returns:
            ``(a, b)`` with ``a`` in seconds and ``b`` in seconds per byte,
            or None with fewer than two different sizes.
        """
        count = len(rows)
        if count < 2:
            return None
        mean_x = sum(row.source for row in rows) / count
        mean_y = sum(row.took for row in rows) / count
        sxx = sum((row.source - mean_x) ** 2 for row in rows)
        if sxx <= 0:
            return None
        slope = sum((row.source - mean_x) * (row.took - mean_y) for row in rows) / sxx
        return mean_y - slope * mean_x, slope

    def _overhead(self, rows: list[LedgerRow], scale: float,
                  given: float | None) -> tuple[float, str]:
        """Find the fixed wall-clock time per video.

        Args:
            rows: Usable ledger entries.
            scale: Wall-clock speed over per-file speed (parallel encodes).
            given: Value set by the caller, or None.

        Returns:
            ``(seconds, basis text)``.
        """
        if given is not None:
            return max(0.0, given), "set with --overhead"
        short = sum(1 for row in rows if (row.length or 0) < self.SHORT)
        fitted = self.fit(rows) if len(rows) >= self.MIN_SAMPLES else None
        if fitted is None or fitted[1] <= 0:
            return 0.0, (f"not measurable ({len(rows)} measured video(s)) - 0 s assumed, "
                         "try --overhead 5 or 10 to see the effect")
        seconds = max(0.0, fitted[0]) / scale
        return seconds, (f"fitted on {len(rows)} measured video(s), {short} of them shorter "
                         f"than {self.SHORT / 60:g} min")

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
                                   rate=self.rates.get(quality, self.overall_rate),
                                   level=level, overhead=self.overhead)
            self.groups[key] = group
        group.files += 1
        group.size += size

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

    def thresholds(self, level: str | None = None) -> list[tuple[str, int, int, float, float]]:
        """Sum up what each ``--min-seconds`` value would take in.

        Args:
            level: Only the videos of this level, or None for all.

        Returns:
            ``(min-seconds label, files, bytes, bytes freed, seconds)`` per
            threshold, from the longest videos only down to all videos.
        """
        result = []
        for low, _high, _label in reversed(self.BANDS):
            taken = [group for group in self.groups.values()
                     if group.band != self.UNKNOWN and self._low(group.band) >= low
                     and level in (None, group.level)]
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

    def table(self, limit: int | None = None) -> list[str]:
        """Format the ranked groups with running totals.

        Args:
            limit: Show at most this many groups (the totals still run over
                the rows shown), or None for all.

        Returns:
            Text lines, header first.
        """
        gib = 1024 ** 3
        lines = [f"{'level':<5} {'length':<10} {'bit rate':<12} {'files':>6} {'GiB':>8} "
                 f"{'freed GiB':>10} {'hours':>7} {'GiB/h':>6} {'sum freed':>10} "
                 f"{'sum hours':>9}"]
        freed = hours = 0.0
        for group in self.ranked()[:limit]:
            freed += group.saved
            hours += group.seconds / 3600
            lines.append(f"{group.level:<5} {group.band:<10} {group.quality:<12} {group.files:>6} "
                         f"{group.size / gib:>8.1f} {group.saved / gib:>10.1f} "
                         f"{group.seconds / 3600:>7.1f} {group.per_hour / gib:>6.1f} "
                         f"{freed / gib:>10.1f} {hours:>9.1f}")
        return lines
