"""Persistent cache of video codecs for fast repeated shrink plans."""
from __future__ import annotations

from typing import Any

from mediatools.core.durationcache import DurationCache


class CodecCache(DurationCache):
    """Remembers the video codec of files keyed by path, size and mtime.

    The shrink plan asks for the codec of every candidate, so that videos
    which are already HEVC can be reported (and left out of volume and time
    estimates) before the start question. Probing costs one ffprobe process
    per file; the cache answers repeated plans almost instantly. An entry is
    used only while size and modification time are unchanged, so a file
    that was shrunk in place is probed again. Storage and lookup are those
    of :class:`DurationCache`; only the value is a codec name.

    Stored as TSV ``path<TAB>size<TAB>mtime_ns<TAB>codec``.
    """

    @staticmethod
    def parse(text: str) -> Any:
        """Convert a stored value.

        Args:
            text: Value column of the TSV file.

        Returns:
            Codec name.
        """
        return text

    @staticmethod
    def format(value: Any) -> str:
        """Convert a value for storage.

        Args:
            value: Codec name.

        Returns:
            The codec name.
        """
        return str(value)
