"""Boxed per-file result table of a shrink run (terminal, log and report file)."""
from __future__ import annotations

import logging
import os
import re
import time

from mediatools.shrink.fileresult import FileResult


class ResultTable:
    """Write one boxed table row per finished video.

    Fixed-width columns hold the figures; the last column holds the path
    relative to the shrink root and takes the remaining width. A longer
    path is wrapped inside its cell at ``/`` boundaries, so the row grows in
    height instead of breaking the frame. Rows are separated by horizontal
    rules::

        ┌──────┬───────┬─────────┬─────────┬─────────┬──────────┬──────────┬────────┬──────────┐
        │   no │ end   │  source │  result │   ratio │   length │     took │ status │ file     │
        ├──────┼───────┼─────────┼─────────┼─────────┼──────────┼──────────┼────────┼──────────┤
        │    1 │ 06:54 │ 2860 MB │  510 MB │  17.8 % │ 44.3 min │ 27.9 min │ shrunk │ a/b/     │
        │      │       │         │         │         │          │          │        │ clip.mp4 │
        └──────┴───────┴─────────┴─────────┴─────────┴──────────┴──────────┴────────┴──────────┘

    The terminal copy uses the terminal width; the report file uses
    :attr:`REPORT_WIDTH` and is written row by row and flushed, so it is
    complete up to the last finished file even after a crash.

    Attributes:
        root: Shrink root; paths are shown relative to it.
        report: Path of the report file.
        log: Logger for the terminal and log file copy.
        width: Total width of the terminal copy.
        count: Number of rows written so far.
    """

    REPORT_WIDTH = 160
    MIN_FILE = 8
    COLUMNS = (("no", 4, ">"), ("end", 5, "<"), ("source", 8, ">"), ("result", 8, ">"),
               ("ratio", 7, ">"), ("length", 8, ">"), ("took", 8, ">"), ("status", 6, "<"))

    def __init__(self, root: str, report: str, log: logging.Logger, width: int = 120) -> None:
        """Create the table; the header is written with the first row.

        Args:
            root: Shrink root.
            report: Path of the report file (created on the first row).
            log: Logger for the terminal and log file copy.
            width: Total width of the terminal copy.
        """
        self.root = root
        self.report = report
        self.log = log
        self.width = width
        self.count = 0

    # -- output ----------------------------------------------------------
    def add(self, item: FileResult) -> None:
        """Write the row of one finished video.

        Args:
            item: Outcome of the video.
        """
        self.count += 1
        cells = self.cells(self.count, item)
        rel = os.path.relpath(item.path, self.root)
        report = self.row(cells, rel, self.REPORT_WIDTH)
        terminal = self.row(cells, rel, self.width)
        if self.count == 1:
            report = [f"shrink root: {self.root}"] + self.head(self.REPORT_WIDTH) + report
            terminal = self.head(self.width) + terminal
            self.log.info("results (also in %s):", self.report)
        else:
            report = [self.rule("├", "┼", "┤", self.REPORT_WIDTH)] + report
            terminal = [self.rule("├", "┼", "┤", self.width)] + terminal
        self._write(report)
        for line in terminal:
            self.log.info("%s", line)

    def finish(self) -> None:
        """Close the frame (only if rows were written since the last call)."""
        if not self.count:
            return
        self._write([self.rule("└", "┴", "┘", self.REPORT_WIDTH)])
        self.log.info("%s", self.rule("└", "┴", "┘", self.width))
        self.count = 0

    def _write(self, lines: list[str]) -> None:
        """Append lines to the report file and flush them.

        Args:
            lines: Lines without line break.
        """
        with open(self.report, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
            handle.flush()

    # -- layout ----------------------------------------------------------
    def file_width(self, width: int) -> int:
        """Width of the file column for a total table width.

        Args:
            width: Total width of the table.

        Returns:
            Characters available for the path (at least :attr:`MIN_FILE`).
        """
        fixed = sum(size + 3 for _name, size, _align in self.COLUMNS) + 4
        return max(self.MIN_FILE, width - fixed)

    def head(self, width: int) -> list[str]:
        """Opening rule, column titles and the rule below them.

        Args:
            width: Total width of the table.

        Returns:
            Three lines.
        """
        titles = [name for name, _size, _align in self.COLUMNS]
        return [self.rule("┌", "┬", "┐", width),
                self._line(titles, "file", width),
                self.rule("├", "┼", "┤", width)]

    def row(self, cells: list[str], path: str, width: int) -> list[str]:
        """Lines of one row; the path is wrapped inside its cell.

        Args:
            cells: Texts of the fixed columns.
            path: Relative path of the video.
            width: Total width of the table.

        Returns:
            One line per path piece; figures only on the first.
        """
        pieces = self.wrap(path, self.file_width(width))
        blank = [""] * len(cells)
        return [self._line(cells if index == 0 else blank, piece, width)
                for index, piece in enumerate(pieces)]

    def rule(self, left: str, cross: str, right: str, width: int) -> str:
        """Horizontal rule matching the columns.

        Args:
            left: Left corner or tee.
            cross: Crossing between columns.
            right: Right corner or tee.
            width: Total width of the table.

        Returns:
            The rule.
        """
        parts = ["─" * (size + 2) for _name, size, _align in self.COLUMNS]
        parts.append("─" * (self.file_width(width) + 2))
        return left + cross.join(parts) + right

    def _line(self, cells: list[str], path: str, width: int) -> str:
        """One framed text line.

        Args:
            cells: Texts of the fixed columns.
            path: Text of the file column.
            width: Total width of the table.

        Returns:
            The line.
        """
        parts = [f" {text:{align}{size}} "
                 for text, (_name, size, align) in zip(cells, self.COLUMNS)]
        parts.append(f" {path:<{self.file_width(width)}} ")
        return "│" + "│".join(parts) + "│"

    @staticmethod
    def wrap(path: str, width: int) -> list[str]:
        """Split a path into pieces of at most ``width`` characters.

        Breaks after a ``/`` or a space where possible; only a single word
        longer than ``width`` is cut hard.

        Args:
            path: Relative path.
            width: Maximum piece length.

        Returns:
            At least one piece.
        """
        tokens = re.findall(r"[^/ ]*[/ ]|[^/ ]+$", path)
        pieces: list[str] = []
        current = ""
        for token in tokens:
            if len(current) + len(token.rstrip(" ")) > width and current:
                pieces.append(current.rstrip(" "))
                current = ""
            while len(token.rstrip(" ")) > width:       # very long single word
                pieces.append(token[:width])
                token = token[width:]
            current += token
        pieces.append(current.rstrip(" "))
        return [piece for piece in pieces if piece] or [""]

    # -- cell texts ------------------------------------------------------
    def cells(self, number: int, item: FileResult) -> list[str]:
        """Texts of the fixed columns.

        Args:
            number: Row number.
            item: Outcome of the video.

        Returns:
            One text per entry of :attr:`COLUMNS`.
        """
        ratio = (f"{item.result / item.source * 100:.1f} %"
                 if item.result is not None and item.source else "-")
        return [str(number), time.strftime("%H:%M"), self.mb(item.source),
                self.mb(item.result), ratio, self.minutes(item.length),
                self.minutes(item.took), item.status]

    @staticmethod
    def mb(size: int | None) -> str:
        """Format a size in MB (10^6 bytes).

        Args:
            size: Size in bytes, or None.

        Returns:
            Text such as ``2860 MB`` or ``-``.
        """
        return "-" if size is None else f"{size / 1e6:.0f} MB"

    @staticmethod
    def minutes(seconds: float | None) -> str:
        """Format a duration in minutes.

        Args:
            seconds: Duration in seconds, or None.

        Returns:
            Text such as ``44.3 min`` or ``-``.
        """
        return "-" if seconds is None else f"{seconds / 60:.1f} min"
