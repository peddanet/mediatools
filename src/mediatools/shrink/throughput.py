"""Measured encoding speed, used for time estimates."""
from __future__ import annotations

import json
import os

from mediatools.core.datadir import DataDir


class ThroughputLog:
    """Accumulates encoding speed and size reduction of finished files.

    Speed (source bytes per second of encoding) and size ratio (output
    bytes per source byte) depend on CPU, codec settings and material;
    measured on the user's own machine they are the best basis for the
    estimates shown before a run. Stored as JSON in ``<data>/state/``:
    ``{"bytes", "seconds"}`` for the speed and ``{"ratio_src", "ratio_out",
    "files"}`` for the size ratio (the ratio keys were added in 1.2.1, so
    older files carry only the speed).

    Attributes:
        path: Location of the JSON file.
        bytes: Accumulated source bytes.
        seconds: Accumulated encoding seconds.
        ratio_src: Source bytes of the files whose output size is known.
        ratio_out: Output bytes of those files.
        files: Number of those files.
    """

    def __init__(self, path: str | None = None):
        """Load the accumulated values.

        Args:
            path: JSON file; ``<data>/state/shrink_throughput.json`` if None.
        """
        self.path = path or os.path.join(DataDir().sub("state"), "shrink_throughput.json")
        self.bytes = 0
        self.seconds = 0.0
        self.ratio_src = 0
        self.ratio_out = 0
        self.files = 0
        try:
            with open(self.path, encoding="utf-8") as handle:
                data = json.load(handle)
            self.bytes = int(data.get("bytes", 0))
            self.seconds = float(data.get("seconds", 0.0))
            self.ratio_src = int(data.get("ratio_src", 0))
            self.ratio_out = int(data.get("ratio_out", 0))
            self.files = int(data.get("files", 0))
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

    def ratio(self) -> float | None:
        """Return the measured size ratio.

        Returns:
            Output bytes per source byte, or None without measurements.
        """
        if self.ratio_src <= 0:
            return None
        return self.ratio_out / self.ratio_src

    def add(self, size: int, seconds: float, out_size: int | None = None) -> None:
        """Add one encoded file and save.

        Args:
            size: Source size in bytes.
            seconds: Encoding time in seconds.
            out_size: Output size in bytes; None leaves the ratio unchanged.
        """
        self.bytes += size
        self.seconds += seconds
        if out_size is not None and size > 0:
            self.ratio_src += size
            self.ratio_out += out_size
            self.files += 1
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"bytes": self.bytes, "seconds": round(self.seconds, 1),
                       "ratio_src": self.ratio_src, "ratio_out": self.ratio_out,
                       "files": self.files}, handle)

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
