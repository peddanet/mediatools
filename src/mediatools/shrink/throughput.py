"""Measured encoding speed, used for time estimates."""
from __future__ import annotations

import json
import os

from mediatools.core.datadir import DataDir


class ThroughputLog:
    """Accumulates how many source bytes were encoded in how many seconds.

    The ratio (source bytes per second of encoding) depends on CPU, codec
    settings and material; measured on the user's own machine it is the
    best basis for estimating the remaining time. Stored as JSON
    ``{"bytes": int, "seconds": float}`` in ``<data>/state/``.

    Attributes:
        path: Location of the JSON file.
        bytes: Accumulated source bytes.
        seconds: Accumulated encoding seconds.
    """

    def __init__(self, path: str | None = None):
        """Load the accumulated values.

        Args:
            path: JSON file; ``<data>/state/shrink_throughput.json`` if None.
        """
        self.path = path or os.path.join(DataDir().sub("state"), "shrink_throughput.json")
        self.bytes = 0
        self.seconds = 0.0
        try:
            with open(self.path, encoding="utf-8") as handle:
                data = json.load(handle)
            self.bytes = int(data.get("bytes", 0))
            self.seconds = float(data.get("seconds", 0.0))
        except (FileNotFoundError, ValueError):
            pass

    def rate(self) -> float | None:
        """Return the measured speed.

        Returns:
            Source bytes per second, or None without measurements.
        """
        if self.bytes <= 0 or self.seconds <= 0:
            return None
        return self.bytes / self.seconds

    def add(self, size: int, seconds: float) -> None:
        """Add one encoded file and save.

        Args:
            size: Source size in bytes.
            seconds: Encoding time in seconds.
        """
        self.bytes += size
        self.seconds += seconds
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"bytes": self.bytes, "seconds": round(self.seconds, 1)}, handle)

    @staticmethod
    def duration_text(seconds: float) -> str:
        """Format a duration for humans.

        Args:
            seconds: Duration in seconds.

        Returns:
            Text such as ``3 d 4 h``, ``5 h 12 min`` or ``42 min``.
        """
        minutes = int(seconds // 60)
        hours, minutes = divmod(minutes, 60)
        days, hours = divmod(hours, 24)
        if days:
            return f"{days} d {hours} h"
        if hours:
            return f"{hours} h {minutes} min"
        return f"{minutes} min"
