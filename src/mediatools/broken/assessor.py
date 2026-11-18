"""Trial decode that measures how much of a damaged video can be used."""
from __future__ import annotations

import re
import shutil
import subprocess

from mediatools.broken.assessment import Assessment
from mediatools.core.decodecheck import DecodeCheck
from mediatools.core.probe import MediaProbe


class Assessor:
    """Decodes a file once and measures it.

    One ffmpeg run decodes the first video stream and every audio stream to
    nothing (``-f null``) and passes the picture through two filters of
    ffmpeg:

    * ``freezedetect`` reports intervals in which the picture does not
      change (a grey or otherwise still image),
    * ``blackdetect`` reports intervals with a black picture.

    The progress output (``-progress pipe:1``, key ``out_time_us``) tells
    how far decoding got; a cut-off file stops early. Messages are read with
    the log flag ``level``, which puts ``[error]`` in front of every error
    line, so errors can be counted while the filters report at level info.
    Still intervals of both filters are merged before they are added up, so
    a black frozen part counts once. A freeze that lasts until the end is
    reported by ffmpeg only with its start; it is closed at the decoded end.
    Lines of the null muxer (irregular time stamps) are not counted as
    errors (:meth:`DecodeCheck.relevant`): they do not show damaged data.

    The run is read-only and uses the lowest CPU priority (``nice`` 19).

    Attributes:
        ffmpeg: Name or path of the ffmpeg executable.
        probe: Probe for the container duration and the video stream.
        timeout_factor: Time limit as a multiple of the duration.
        timeout_min: Minimum time limit in seconds.
    """

    FILTERS = "freezedetect=n=-60dB:d=2,blackdetect=d=2:pix_th=0.10"
    TIMEOUT_MIN = 600.0
    TIMEOUT_FACTOR = 2.0

    _FREEZE_START = re.compile(r"freezedetect\.freeze_start:\s*([\d.]+)")
    _FREEZE_END = re.compile(r"freezedetect\.freeze_end:\s*([\d.]+)")
    _BLACK = re.compile(r"black_start:\s*([\d.]+)\s+black_end:\s*([\d.]+)")

    def __init__(self, ffmpeg: str = "ffmpeg", probe: MediaProbe | None = None,
                 timeout_min: float = TIMEOUT_MIN, timeout_factor: float = TIMEOUT_FACTOR):
        """Store the settings.

        Args:
            ffmpeg: Name or path of the ffmpeg executable.
            probe: Probe; a new one if None.
            timeout_min: Minimum time limit in seconds.
            timeout_factor: Time limit as a multiple of the duration.
        """
        self.ffmpeg = ffmpeg
        self.probe = probe or MediaProbe()
        self.timeout_min = timeout_min
        self.timeout_factor = timeout_factor

    def command(self, path: str) -> list[str]:
        """Build the ffmpeg command of the measurement.

        Args:
            path: Video file.

        Returns:
            Argument list for :func:`subprocess.run` (no shell).
        """
        prefix = ["nice", "-n", "19"] if shutil.which("nice") else []
        return prefix + [self.ffmpeg, "-hide_banner", "-nostdin", "-nostats",
                         "-loglevel", "level+info", "-progress", "pipe:1", "-i", path,
                         "-map", "0:v:0", "-map", "0:a?", "-vf", self.FILTERS,
                         "-f", "null", "-"]

    def assess(self, path: str) -> Assessment:
        """Measure a video.

        Args:
            path: Video file.

        Returns:
            The assessment; ``opened`` is False if ffprobe finds no video
            stream or ffmpeg cannot open the file.
        """
        if not self.probe.has_video(path):
            return Assessment(False, note="no readable video stream")
        duration = self.probe.duration(path) or 0.0
        limit = max(self.timeout_min, duration * self.timeout_factor)
        try:
            res = subprocess.run(self.command(path), capture_output=True, text=True,
                                 errors="replace", check=False, timeout=limit)
        except subprocess.TimeoutExpired:
            return Assessment(True, duration, note=f"decode did not finish within {limit:.0f} s")
        decoded = self.decoded_seconds(res.stdout)
        errors = [line for line in DecodeCheck.relevant(res.stderr.splitlines())
                  if "[error]" in line or "[fatal]" in line]
        if decoded == 0 and res.returncode != 0:
            return Assessment(False, duration, errors=len(errors),
                              note=self.clean(errors[-1]) if errors else "ffmpeg failed")
        still = self.still_seconds(res.stderr, decoded)
        return Assessment(True, duration, decoded, still, len(errors),
                          self.clean(errors[0]) if errors else "")

    @staticmethod
    def decoded_seconds(progress: str) -> float:
        """Read how far decoding got from ffmpeg's progress output.

        Args:
            progress: Text of ``-progress pipe:1``.

        Returns:
            Last ``out_time_us`` in seconds; 0.0 if there is none.
        """
        last = 0
        for line in progress.splitlines():
            if line.startswith("out_time_us="):
                value = line.split("=", 1)[1].strip()
                if value.isdigit():
                    last = int(value)
        return last / 1e6

    @classmethod
    def still_seconds(cls, messages: str, decoded: float) -> float:
        """Add up the frozen and black intervals without counting overlaps.

        Args:
            messages: ffmpeg's message output.
            decoded: Decoded seconds; closes an interval without an end.

        Returns:
            Seconds with a still or black picture.
        """
        spans: list[tuple[float, float]] = []
        start: float | None = None
        for line in messages.splitlines():
            if (match := cls._FREEZE_START.search(line)) is not None:
                start = float(match.group(1))
            elif (match := cls._FREEZE_END.search(line)) is not None and start is not None:
                spans.append((start, float(match.group(1))))
                start = None
            elif (match := cls._BLACK.search(line)) is not None:
                spans.append((float(match.group(1)), float(match.group(2))))
        if start is not None:
            spans.append((start, decoded))
        return cls.merged_length(spans, decoded)

    @staticmethod
    def merged_length(spans: list[tuple[float, float]], limit: float) -> float:
        """Return the length of the union of intervals, cut at a limit.

        Args:
            spans: ``(start, end)`` intervals.
            limit: Upper bound of every interval.

        Returns:
            Total length in seconds.
        """
        total, reach = 0.0, 0.0
        for begin, end in sorted((max(0.0, a), min(limit, b)) for a, b in spans):
            begin = max(begin, reach)
            if end > begin:
                total += end - begin
                reach = end
        return total

    @staticmethod
    def clean(line: str) -> str:
        """Strip the component and level tags of an ffmpeg message.

        Args:
            line: Message line such as ``[h264 @ 0x1] [error] text``.

        Returns:
            The text, at most 200 characters.
        """
        return re.sub(r"^(\[[^\]]*\]\s*)+", "", line.strip())[:200]
