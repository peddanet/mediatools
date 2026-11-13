"""One entry of the ledger: a finished video."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LedgerRow:
    """A video finished by a shrink run, as kept in ``ledger.tsv``.

    Attributes:
        when: End time, ``YYYY-MM-DD HH:MM:SS``.
        session: Session name (log file name without ``.log``).
        status: ``shrunk``, ``copy``, ``FAILED``, ``BROKEN`` or ``HEVC``.
        source: Size before, in bytes.
        result: Size after, in bytes, or None.
        took: Wall-clock seconds spent on the file.
        length: Playing time in seconds, or None if unknown.
        mtime: Modification time of the original (epoch seconds), or None
            if unknown (rows filled in from logs).
        path: Absolute path of the video.
    """

    when: str
    session: str
    status: str
    source: int
    result: int | None
    took: float
    length: float | None
    mtime: float | None
    path: str

    FIELDS = 9

    @staticmethod
    def _optional(text: str, kind: type) -> float | int | None:
        """Parse an optional number.

        Args:
            text: Field text; empty for None.
            kind: ``int`` or ``float``.

        Returns:
            The number, or None.
        """
        return kind(text) if text else None

    @staticmethod
    def _text(value: float | int | None, fmt: str) -> str:
        """Format an optional number.

        Args:
            value: The number, or None.
            fmt: Format specification.

        Returns:
            The text; empty for None.
        """
        return "" if value is None else format(value, fmt)

    def line(self) -> str:
        """Return the TSV line (without line break).

        Tabs and line breaks in the path are replaced by spaces.

        Returns:
            Nine tab-separated fields.
        """
        num = self._text
        path = self.path.replace("\t", " ").replace("\n", " ").replace("\r", " ")
        return "\t".join([self.when, self.session, self.status, str(self.source),
                          num(self.result, "d"), f"{self.took:.1f}", num(self.length, ".1f"),
                          num(self.mtime, ".0f"), path])

    @classmethod
    def parse(cls, line: str) -> LedgerRow | None:
        """Parse one TSV line.

        Args:
            line: Line of ``ledger.tsv``.

        Returns:
            The entry, or None for a comment or a damaged line.
        """
        parts = line.rstrip("\n").split("\t")
        if line.startswith("#") or len(parts) != cls.FIELDS:
            return None
        try:
            return cls(parts[0], parts[1], parts[2], int(parts[3]),
                       cls._optional(parts[4], int),  # type: ignore[arg-type]
                       float(parts[5] or 0.0), cls._optional(parts[6], float),
                       cls._optional(parts[7], float), parts[8])
        except ValueError:
            return None
