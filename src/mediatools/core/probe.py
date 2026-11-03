"""Thin wrapper around ffprobe."""
from __future__ import annotations

import shutil
import subprocess


class MediaProbe:
    """Reads technical properties of media files with ``ffprobe``.

    Every method returns ``None`` (or False) instead of raising when ffprobe
    cannot read a file, so callers decide how to treat unreadable media.

    Attributes:
        ffprobe: Name or path of the ffprobe executable.
    """

    def __init__(self, ffprobe: str = "ffprobe"):
        """Store the executable name.

        Args:
            ffprobe: Name or path of the ffprobe executable.
        """
        self.ffprobe = ffprobe

    def available(self) -> bool:
        """Tell whether ffprobe can be found.

        Returns:
            True if the executable is found in ``PATH``.
        """
        return shutil.which(self.ffprobe) is not None

    def value(self, path: str, entry: str, stream: str | None = None) -> str | None:
        """Return a single ffprobe value.

        Args:
            path: Media file.
            entry: ffprobe ``-show_entries`` argument, e.g. ``format=duration``.
            stream: Optional stream selector, e.g. ``v:0``.

        Returns:
            First value printed by ffprobe, or None if ffprobe failed or
            printed nothing.
        """
        cmd = [self.ffprobe, "-v", "error"]
        if stream:
            cmd += ["-select_streams", stream]
        cmd += ["-show_entries", entry,
                "-of", "default=noprint_wrappers=1:nokey=1", path]
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode != 0:
            return None
        first = res.stdout.strip().split("\n")[0].strip()
        return first or None

    def duration(self, path: str) -> float | None:
        """Return the container duration.

        Args:
            path: Media file.

        Returns:
            Duration in seconds, or None if it cannot be determined.
        """
        text = self.value(path, "format=duration")
        if text is None:
            return None
        try:
            return float(text)
        except ValueError:          # e.g. "N/A" for streams without duration
            return None

    def video_codec(self, path: str) -> str | None:
        """Return the codec of the first video stream.

        Args:
            path: Media file.

        Returns:
            Codec name such as ``h264`` or ``hevc``, or None without video.
        """
        return self.value(path, "stream=codec_name", "v:0")

    def has_video(self, path: str) -> bool:
        """Tell whether a decodable video stream exists.

        Args:
            path: Media file.

        Returns:
            True if ffprobe reports a codec for stream ``v:0``.
        """
        return self.video_codec(path) is not None
