"""Rebuild ledger entries from the result tables of shrink logs."""
from __future__ import annotations

import os
import re

from mediatools.shrink.ledgerrow import LedgerRow
from mediatools.shrink.savingslog import SavingsLog


class LogBackfill:
    """Reads the boxed result tables of shrink logs written before the ledger.

    Each result row starts with its number and may continue on further
    lines that carry only the next piece of the path (the path is wrapped
    after ``/`` or a space, see :class:`ResultTable`). The shrink root is
    taken from the line ``shrink root: <root>  mode: ...`` that every run
    writes first. Sizes are in MB (10^6 bytes, rounded), times in minutes
    with one decimal; the modification time of the original is unknown.

    Attributes:
        folder: Folder of the ``shrink_*.log`` files.
    """

    LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)(?:,\d+)? \S+\s+(│.*│)\s*$")
    ROOT = re.compile(r"shrink root: (.+?)  mode: ")
    STATUSES = ("shrunk", "copy", "FAILED", "BROKEN", "HEVC")
    CELLS = 9

    def __init__(self, folder: str):
        """Choose the log folder.

        Args:
            folder: Folder with the shrink logs.
        """
        self.folder = folder

    def logs(self) -> list[str]:
        """Return the shrink logs, oldest first.

        Returns:
            Paths.
        """
        return SavingsLog(self.folder).logs()

    def rows(self) -> list[LedgerRow]:
        """Return the finished videos of all logs.

        Returns:
            Ledger entries, oldest first.
        """
        result: list[LedgerRow] = []
        for path in self.logs():
            result += self.read(path)
        return result

    @staticmethod
    def number(text: str, unit: str) -> float | None:
        """Parse a figure such as ``1148 MB`` or ``44.3 min``.

        Args:
            text: Cell text.
            unit: Expected unit.

        Returns:
            The number, or None for ``-``.
        """
        text = text.strip()
        if not text.endswith(unit):
            return None
        try:
            return float(text[:-len(unit)].strip())
        except ValueError:
            return None

    def read(self, path: str) -> list[LedgerRow]:
        """Return the finished videos of one log.

        Args:
            path: Log file.

        Returns:
            Ledger entries in log order.
        """
        session = os.path.basename(path)[:-4]
        root = ""
        rows: list[LedgerRow] = []
        pieces: list[str] = []
        width = 0
        current: LedgerRow | None = None
        try:
            handle = open(path, encoding="utf-8", errors="replace")
        except OSError:
            return []
        with handle:
            for line in handle:
                found = self.ROOT.search(line)
                if found:
                    root = found.group(1)
                match = self.LINE.match(line)
                cells = match.group(2).split("│")[1:-1] if match else []
                if len(cells) != self.CELLS:
                    if current is not None:
                        rows.append(self._close(current, pieces, root))
                        current = None
                    continue
                if cells[0].strip().isdigit() and cells[7].strip() in self.STATUSES:
                    if current is not None:
                        rows.append(self._close(current, pieces, root))
                    current = self._start(match.group(1), session, cells) if match else None
                    width = len(cells[8]) - 2
                    pieces = [cells[8].strip()]
                elif current is not None and not cells[0].strip():
                    pieces.append(cells[8].strip())
                    pieces[-2] += "\0" if len(pieces[-2]) >= width else ""
        if current is not None:
            rows.append(self._close(current, pieces, root))
        return rows

    def _start(self, when: str, session: str, cells: list[str]) -> LedgerRow:
        """Create the entry of a result row (path follows).

        Args:
            when: Time stamp of the log line.
            session: Session name.
            cells: The nine cells.

        Returns:
            Entry without path.
        """
        source = self.number(cells[2], "MB")
        result = self.number(cells[3], "MB")
        length = self.number(cells[5], "min")
        took = self.number(cells[6], "min")
        return LedgerRow(when, session, cells[7].strip(), int((source or 0) * 1e6),
                         None if result is None else int(result * 1e6),
                         (took or 0.0) * 60, None if length is None else length * 60, None, "")

    @staticmethod
    def _close(row: LedgerRow, pieces: list[str], root: str) -> LedgerRow:
        """Join the path pieces and make the path absolute.

        A piece that fills its cell completely was cut inside a word (marked
        with a trailing NUL) and is joined without a space, as is a piece
        ending with ``/``; other pieces were broken at a space.

        Args:
            row: Entry without path.
            pieces: Path pieces of the row.
            root: Shrink root of the log.

        Returns:
            The entry with its path.
        """
        rel = ""
        glue = False
        for piece in pieces:
            rel += piece.rstrip("\0") if not rel or glue or rel.endswith("/") \
                else " " + piece.rstrip("\0")
            glue = piece.endswith("\0")
        row.path = os.path.join(root, rel) if root else rel
        return row
