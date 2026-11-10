"""Typed collection of the helpers a shrink run uses."""
from __future__ import annotations

from typing import TypedDict

from mediatools.core.codeccache import CodecCache
from mediatools.core.decodecheck import DecodeCheck
from mediatools.core.selfrestart import SelfRestart
from mediatools.core.durationcache import DurationCache
from mediatools.shrink.resulttable import ResultTable
from mediatools.shrink.spacetally import SpaceTally
from mediatools.shrink.throughput import ThroughputLog


class ShrinkState(TypedDict):
    """Helpers of a shrink run, kept together in one attribute.

    Attributes:
        durations: Cache of playing times (for ``min_seconds``), or None.
        codecs: Cache of video codecs (HEVC check of the plan), or None.
        throughput: Measured speed and size ratio for the estimates.
        results: Result table of finished videos, or None.
        decode: Trial decode that sets aside undecodable videos, or None
            (no check).
        ffmpeg_logs: Folder for ffmpeg's messages of failed encodes, or
            None (they are deleted).
        restart: Switch to a newly installed release on request, or None
            (requests are ignored).
        space: Space read, written and freed, this run and overall.
    """

    durations: DurationCache | None
    codecs: CodecCache | None
    throughput: ThroughputLog
    results: ResultTable | None
    decode: DecodeCheck | None
    ffmpeg_logs: str | None
    restart: SelfRestart | None
    space: SpaceTally
