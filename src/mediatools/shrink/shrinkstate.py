"""Typed collection of the helpers a shrink run uses."""
from __future__ import annotations

from typing import TypedDict

from mediatools.core.codeccache import CodecCache
from mediatools.core.durationcache import DurationCache
from mediatools.shrink.resulttable import ResultTable
from mediatools.shrink.throughput import ThroughputLog


class ShrinkState(TypedDict):
    """Helpers of a shrink run, kept together in one attribute.

    Attributes:
        durations: Cache of playing times (for ``min_seconds``), or None.
        codecs: Cache of video codecs (HEVC check of the plan), or None.
        throughput: Measured speed and size ratio for the estimates.
        results: Result table of finished videos, or None.
    """

    durations: DurationCache | None
    codecs: CodecCache | None
    throughput: ThroughputLog
    results: ResultTable | None
