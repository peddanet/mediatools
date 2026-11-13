"""Which videos a shrink job covers: root and filters."""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime

from mediatools.core.mediatypes import MediaTypes
from mediatools.shrink.ledgerrow import LedgerRow


@dataclass(frozen=True)
class JobSpec:
    """The set of videos of a shrink job.

    A *job* is defined by what it selects - the root directory and the
    filters - not by how it encodes: the CRF, the order and the number of
    parallel encodes are settings of a run and may change between runs of
    the same job. Every file finished by any run counts for each job whose
    set contains it (:meth:`matches`), so a job that is continued with
    other settings, or a subtree shrunk on its own, is counted correctly.

    Attributes:
        root: Absolute start directory.
        extensions: Processed extensions (lower case, with dot).
        min_seconds: Shortest playing time; 0 selects all.
        min_size: Smallest file size in bytes; 0 selects all.
        newer_than: Earliest modification time (epoch seconds), or None.
    """

    root: str
    extensions: frozenset = field(default_factory=lambda: frozenset({".mp4"}))
    min_seconds: float = 0.0
    min_size: int = 0
    newer_than: float | None = None

    UNITS = {"": 1, "K": 10 ** 3, "M": 10 ** 6, "G": 10 ** 9, "T": 10 ** 12}
    READ = ("shrunk", "copy", "FAILED")
    """Statuses that cost an encode (counted as read)."""

    # -- parsing ---------------------------------------------------------
    @classmethod
    def parse_size(cls, text: str) -> int:
        """Parse a size such as ``300M`` (decimal units, like the MB of the logs).

        Args:
            text: Number with an optional unit K, M, G or T (B optional).

        Returns:
            Bytes.

        Raises:
            ValueError: If the text is not a size.
        """
        match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*([KMGTkmgt]?)[Bb]?\s*", text or "")
        if not match:
            raise ValueError(f"not a size: {text!r} (examples: 300M, 1.5G)")
        return int(float(match.group(1)) * cls.UNITS[match.group(2).upper()])

    @staticmethod
    def parse_date(text: str) -> float:
        """Parse a date ``YYYY-MM-DD`` (local midnight).

        Args:
            text: The date.

        Returns:
            Epoch seconds.

        Raises:
            ValueError: If the text is not such a date.
        """
        try:
            return datetime.strptime(text.strip(), "%Y-%m-%d").timestamp()
        except (ValueError, AttributeError):
            raise ValueError(f"not a date: {text!r} (format YYYY-MM-DD)") from None

    @staticmethod
    def size_text(size: int) -> str:
        """Format a size with the largest whole decimal unit.

        Args:
            size: Bytes.

        Returns:
            Text such as ``300M``.
        """
        for unit, factor in (("T", 10 ** 12), ("G", 10 ** 9), ("M", 10 ** 6), ("K", 10 ** 3)):
            if size >= factor and size % factor == 0:
                return f"{size // factor}{unit}"
        if size >= 10 ** 6:
            return f"{size / 10 ** 6:g}M"
        return str(size)

    # -- identity --------------------------------------------------------
    def as_dict(self) -> dict[str, object]:
        """Return the job as plain values (JSON).

        Returns:
            Root, sorted extensions and the filters.
        """
        return {"root": self.root, "ext": sorted(self.extensions),
                "min_seconds": self.min_seconds, "min_size": self.min_size,
                "newer_than": (datetime.fromtimestamp(self.newer_than).strftime("%Y-%m-%d")
                               if self.newer_than is not None else None)}

    @classmethod
    def from_dict(cls, data: dict) -> JobSpec:
        """Rebuild a job from :meth:`as_dict`.

        Args:
            data: Plain values.

        Returns:
            The job.
        """
        newer = data.get("newer_than")
        return cls(str(data["root"]), frozenset(data.get("ext") or [".mp4"]),
                   float(data.get("min_seconds") or 0.0), int(data.get("min_size") or 0),
                   cls.parse_date(newer) if newer else None)

    @property
    def key(self) -> str:
        """Short stable identifier of the job.

        Returns:
            12 hex characters derived from :meth:`as_dict`.
        """
        text = json.dumps(self.as_dict(), sort_keys=True)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]

    def filters(self) -> list[str]:
        """Return the filters as command-line options.

        Returns:
            Options that differ from the defaults, e.g. ``--min-size 300M``.
        """
        args: list[str] = []
        if self.extensions != frozenset({".mp4"}):
            args += ["--ext", ",".join(ext.lstrip(".") for ext in sorted(self.extensions))]
        if self.min_seconds:
            args += ["--min-seconds", f"{self.min_seconds:g}"]
        if self.min_size:
            args += ["--min-size", self.size_text(self.min_size)]
        if self.newer_than is not None:
            args += ["--newer-than", str(self.as_dict()["newer_than"])]
        return args

    def label(self) -> str:
        """Return a one-line description.

        Returns:
            Root followed by the filters, e.g. ``/media/c  --min-size 300M``.
        """
        filters = " ".join(self.filters())
        return f"{self.root}  {filters}" if filters else self.root

    # -- membership ------------------------------------------------------
    def contains(self, path: str) -> bool:
        """Return whether a path lies below the root with a processed extension.

        Args:
            path: Absolute file path.

        Returns:
            True if the path is part of the tree and extension set.
        """
        root = self.root.rstrip(os.sep) or os.sep
        inside = path.startswith(root + os.sep) or root == os.sep
        return inside and MediaTypes.extension(os.path.basename(path)) in self.extensions

    def matches(self, row: LedgerRow) -> bool:
        """Return whether a finished file belongs to this job.

        Unknown values (playing time of an old log row, modification time
        of rows filled in from logs) pass a playing-time filter but fail a
        date filter, which would otherwise count files of unknown age.

        Args:
            row: Ledger entry.

        Returns:
            True if the file is part of the job's set.
        """
        if not self.contains(row.path) or row.source < self.min_size:
            return False
        if self.min_seconds and row.length is not None and row.length < self.min_seconds:
            return False
        if self.newer_than is not None and (row.mtime is None or row.mtime < self.newer_than):
            return False
        return True
