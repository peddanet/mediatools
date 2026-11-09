"""Outcome of one video in a shrink run (a row of the result table)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FileResult:
    """Outcome of one video.

    Attributes:
        path: Absolute path of the video.
        status: ``shrunk``, ``copy`` (review mode), ``HEVC`` (recorded
            without encoding) or ``FAILED``.
        source: Size before, in bytes.
        result: Size after, in bytes, or None (failed, HEVC).
        length: Playing time in seconds, or None if unknown.
        took: Wall-clock seconds spent on the file.
    """

    path: str
    status: str
    source: int
    result: int | None = None
    length: float | None = None
    took: float = 0.0
