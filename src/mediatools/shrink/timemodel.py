"""Encoding time of a video from its playing time, fitted on the ledger."""
from __future__ import annotations

import math
from collections.abc import Callable

from mediatools.shrink.ledgerrow import LedgerRow


class TimeModel:
    """Expected wall-clock time to shrink videos, with a fixed part per video.

    The encoder works frame by frame, so the time of a video grows with its
    **playing time**, not with its size: a long video with a low bit rate
    is small but slow. Every video also costs a part that does not depend
    on its length - start of ffmpeg, trial decode, validation, flushing.
    The ledger time of each shrunk video is therefore fitted as::

        took = a + b_q x length

    with one fixed part ``a`` shared by all videos and one slope ``b_q`` per
    bit-rate class ``q`` (least squares with a shared intercept, closed
    form). Classes with fewer than :attr:`MIN_SAMPLES` measured videos use
    the slope over all videos.

    **Measured or extrapolated.** ``a`` is the value at length zero. It is
    only *measured* when the ledger holds at least :attr:`MIN_SAMPLES`
    videos shorter than :attr:`SHORT` seconds; otherwise it is extrapolated
    from long videos and may be far off. In that case the model keeps two
    variants - without a fixed part (``low``) and with the extrapolated one
    (``high``) - and the estimate shows the time as a range. A value set by
    the caller (``--overhead``) replaces the fit.

    All times are scaled to wall-clock time with ``scale`` (measured
    wall-clock speed over the per-file speed, i.e. the effect of parallel
    encodes).

    Attributes:
        fixed: Fixed wall-clock seconds per video, cautious variant.
        fixed_low: Fixed wall-clock seconds per video, optimistic variant.
        slopes: Per bit-rate class: wall seconds per second of playing
            time, ``(cautious, optimistic)``.
        pooled: The same over all measured videos.
        byte_rate: Bytes per second of playing time of the measured videos
            (to guess the length of a video whose length is unknown).
        measured: Number of usable ledger entries.
        short: How many of them are shorter than :attr:`SHORT`.
        basis: How the fixed part was found (for the report).
    """

    MIN_SAMPLES = 20
    SHORT = 300.0
    DEFAULT_SLOPE = 1.0          # wall second per second of playing time without data
    DEFAULT_BYTE_RATE = 5.0e5    # 4 Mbit/s

    def __init__(self, rows: list[LedgerRow], classify: Callable[[int, float | None], str],
                 scale: float = 1.0,
                 given: float | None = None) -> None:
        """Fit the model.

        Args:
            rows: Usable ledger entries (shrunk, with length and time).
            classify: ``(size, length) -> class label`` of a video.
            scale: Wall-clock speed over per-file speed.
            given: Fixed wall-clock seconds per video set by the caller, or
                None to fit it.
        """
        self.measured = len(rows)
        self.short = sum(1 for row in rows if (row.length or 0) < self.SHORT)
        sums: dict[str, list[float]] = {}
        for row in rows:
            length = float(row.length or 0)
            entry = sums.setdefault(classify(row.source, row.length), [0.0] * 5)
            for index, value in enumerate((1.0, length, length * length, length * row.took,
                                           row.took)):
                entry[index] += value
        total = [sum(entry[index] for entry in sums.values()) for index in range(5)]
        self.byte_rate = (sum(row.source for row in rows) / total[1] if total[1]
                          else self.DEFAULT_BYTE_RATE)
        fitted = self.intercept(list(sums.values()))
        high, low, self.basis = self._fixed(fitted, scale, given)
        self.fixed, self.fixed_low = high, low
        self.pooled = (self.slope(total, high * scale, scale),
                       self.slope(total, low * scale, scale))
        self.slopes = {quality: (self.slope(entry, high * scale, scale),
                                 self.slope(entry, low * scale, scale))
                       for quality, entry in sums.items() if entry[0] >= self.MIN_SAMPLES}

    @staticmethod
    def intercept(classes: list[list[float]]) -> float | None:
        """Return the shared fixed part ``a`` of ``took = a + b_q x length``.

        Args:
            classes: Per class the sums ``n, L, L^2, L x took, took``.

        Returns:
            ``a`` in per-file seconds, or None if it cannot be determined
            (fewer than two videos, or no spread of the lengths).
        """
        count = sum(entry[0] for entry in classes)
        took = sum(entry[4] for entry in classes)
        denominator, numerator = count, took
        for entry in classes:
            if entry[2] > 0:
                denominator -= entry[1] * entry[1] / entry[2]
                numerator -= entry[1] * entry[3] / entry[2]
        if count < 2 or denominator <= 1e-9 * count:
            return None
        return numerator / denominator

    def _fixed(self, fitted: float | None, scale: float,
               given: float | None) -> tuple[float, float, str]:
        """Choose the fixed part and describe how it was found.

        Args:
            fitted: Fitted ``a`` in per-file seconds, or None.
            scale: Wall-clock speed over per-file speed.
            given: Value set by the caller, or None.

        Returns:
            ``(cautious, optimistic, basis)``: fixed wall-clock seconds per
            video and the text for the report.
        """
        short = f"{self.short} of them shorter than {self.SHORT / 60:g} min"
        if given is not None:
            return max(0.0, given), max(0.0, given), "set with --overhead"
        if fitted is None or self.measured < self.MIN_SAMPLES:
            return 0.0, 0.0, (f"not measurable ({self.measured} measured video(s)) - 0 s "
                              "assumed, try --overhead 5 or 10 to see the effect")
        value = max(0.0, fitted) / scale
        if self.short >= self.MIN_SAMPLES:
            return value, value, f"measured on {self.measured} video(s), {short}"
        return value, 0.0, (f"extrapolated from {self.measured} video(s), only {short} - "
                            "times are shown as a range from 0 s to this value")

    def slope(self, sums: list[float], fixed: float, scale: float) -> float:
        """Return wall seconds per second of playing time for given sums.

        Args:
            sums: ``n, L, L^2, L x took, took`` of the videos.
            fixed: Fixed part in per-file seconds.
            scale: Wall-clock speed over per-file speed.

        Returns:
            ``(sum(L x took) - fixed x sum(L)) / sum(L^2)`` in wall seconds,
            at least a hundredth of the plain ratio ``took / L`` (a fixed
            part set too high must not make the encoder infinitely fast);
            :attr:`DEFAULT_SLOPE` without data.
        """
        if sums[2] <= 0 or sums[1] <= 0:
            return self.DEFAULT_SLOPE
        value = (sums[3] - fixed * sums[1]) / sums[2]
        return max(value, sums[4] / sums[1] / 100.0) / scale

    @property
    def uncertain(self) -> bool:
        """True if the time is a range (extrapolated fixed part)."""
        return not math.isclose(self.fixed, self.fixed_low)

    def seconds(self, quality: str, files: int, length: float) -> tuple[float, float]:
        """Return the expected wall-clock time of videos of one class.

        Args:
            quality: Bit-rate class label.
            files: Number of videos.
            length: Their total playing time in seconds.

        Returns:
            ``(cautious, optimistic)`` seconds.
        """
        high, low = self.slopes.get(quality, self.pooled)
        return files * self.fixed + length * high, files * self.fixed_low + length * low

    def speed(self, quality: str) -> float:
        """Return seconds of playing time encoded per wall second (cautious variant).

        Args:
            quality: Bit-rate class label.

        Returns:
            Speed as a multiple of real time.
        """
        return 1.0 / self.slopes.get(quality, self.pooled)[0]
