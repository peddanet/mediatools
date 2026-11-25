"""Encoding speed of the recent running time, read from the ledger."""
from __future__ import annotations

import time

from mediatools.shrink.ledger import Ledger
from mediatools.shrink.ledgerrow import LedgerRow


class RecentSpeed:
    """Speed for the remaining time: bytes per second of recent *running* time.

    Every ledger row knows when its video ended and how long it was encoded,
    i.e. the interval ``[end - took, end]``. The running time is the union of
    these intervals: parallel encodes count once, and time in which nothing
    was encoded - a stop, a crash, a suspended process (Ctrl+Z), the
    planning phase - does not count at all. The speed is the source bytes
    of the newest videos over their running time, taken back until
    :attr:`WINDOW` seconds of running time are covered.

    The ledger outlives the process, so the speed does not start from zero
    after a switch to a new release (``mediatools reload``), and it follows
    a change of the material within hours instead of averaging over the
    whole run.

    Attributes:
        ledger: The ledger of finished videos.
        cache: ``(ledger size, speed)`` of the last computation.
    """

    WINDOW = 3 * 3600.0      # seconds of running time that make up the speed
    MIN_FILES = 5            # fewer finished videos: no speed
    MAX_ROWS = 2000          # newest ledger rows looked at

    def __init__(self, ledger: Ledger) -> None:
        """Bind to the ledger.

        Args:
            ledger: The ledger of finished videos.
        """
        self.ledger = ledger
        self.cache: tuple[int, float | None] = (-1, None)

    @staticmethod
    def span(row: LedgerRow) -> tuple[float, float] | None:
        """Return the encode interval of a row in epoch seconds.

        Args:
            row: A ledger row.

        Returns:
            ``(start, end)``, or None for rows without an encode time.
        """
        if row.status not in ("shrunk", "copy") or row.took <= 0 or row.source <= 0:
            return None
        try:
            end = time.mktime(time.strptime(row.when, "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            return None
        return end - row.took, end

    @staticmethod
    def busy(spans: list[tuple[float, float]]) -> float:
        """Return the length of the union of intervals.

        Args:
            spans: ``(start, end)`` pairs in any order.

        Returns:
            Seconds covered by at least one interval.
        """
        total, start, end = 0.0, None, None
        for begin, finish in sorted(spans):
            if end is None or begin > end:
                if end is not None and start is not None:
                    total += end - start
                start, end = begin, finish
            else:
                end = max(end, finish)
        if end is not None and start is not None:
            total += end - start
        return total

    def rate(self) -> float | None:
        """Return the recent speed while running.

        Returns:
            Source bytes per second of running time, or None with fewer
            than :attr:`MIN_FILES` finished videos.
        """
        rows = self.ledger.rows()
        if self.cache[0] == len(rows):
            return self.cache[1]
        spans: list[tuple[float, float]] = []
        size = 0
        speed = None
        full = False                 # the window is covered
        for row in reversed(rows[-self.MAX_ROWS:]):
            interval = self.span(row)
            if interval is None:
                continue
            if full and interval[1] <= min(start for start, _end in spans):
                break                    # older than everything taken: window complete
            spans.append(interval)
            size += row.source
            full = len(spans) >= self.MIN_FILES and self.busy(spans) >= self.WINDOW
        running = self.busy(spans)
        if len(spans) >= self.MIN_FILES and running > 0:
            speed = size / running
        self.cache = (len(rows), speed)
        return speed
