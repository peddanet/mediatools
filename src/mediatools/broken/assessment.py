"""Measured state of one damaged video and the class derived from it."""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class Assessment:
    """What a trial decode found in a video.

    The class of a video follows from three measurements: the container
    duration, the part of it that decodes (``decoded``) and, inside that
    part, the seconds that show nothing (``still``: a frozen or black
    picture, e.g. grey frames). A video is *hopeless* if it cannot be opened
    at all, if less than :attr:`MIN_DECODED` of its duration decodes, or if
    more than :attr:`MAX_STILL` of the decoded part is still; every other
    video is *promising* and is shown to the user first.

    Attributes:
        opened: The file has a readable container with a video stream.
        duration: Container duration in seconds (0 if unknown).
        decoded: Seconds of video that decoded.
        still: Seconds of the decoded part with a frozen or black picture.
        errors: Number of error lines of the decoder.
        note: First error message or the reason why the file did not open.
        method: Version of the measurement; 1 (mediatools 1.7.0) counted
            the time stamp lines of the null muxer as errors and decoded
            the picture only, 2 (since 1.7.1) does not and decodes the
            sound as well.
    """

    MIN_DECODED = 0.10
    MAX_STILL = 0.90
    REPAIR_FROM = 0.50
    HEALTHY_FROM = 0.99
    METHOD = 2

    opened: bool
    duration: float = 0.0
    decoded: float = 0.0
    still: float = 0.0
    errors: int = 0
    note: str = ""
    method: int = METHOD

    @property
    def decoded_share(self) -> float:
        """Share of the duration that decodes.

        Returns:
            0.0 to 1.0; 0.0 if the duration is unknown.
        """
        if self.duration <= 0:
            return 0.0
        return min(1.0, self.decoded / self.duration)

    @property
    def usable_share(self) -> float:
        """Share of the duration that decodes and shows a moving picture.

        Returns:
            0.0 to 1.0.
        """
        if self.duration <= 0:
            return 0.0
        return max(0.0, min(1.0, (self.decoded - self.still) / self.duration))

    @property
    def kind(self) -> str:
        """Class of the video.

        Returns:
            ``hopeless`` or ``promising``.
        """
        if not self.opened or self.decoded_share < self.MIN_DECODED:
            return "hopeless"
        if self.decoded > 0 and self.still / self.decoded > self.MAX_STILL:
            return "hopeless"
        return "promising"

    @property
    def healthy(self) -> bool:
        """Tell whether the video is not damaged at all (a false alarm).

        Returns:
            True if it opens, at least :attr:`HEALTHY_FROM` of its duration
            decodes, no decoder error was reported and the picture is not
            mostly still (class ``promising``).
        """
        return self.opened and self.errors == 0 and self.decoded_share >= self.HEALTHY_FROM \
            and self.kind == "promising"

    @property
    def current(self) -> bool:
        """Tell whether the measurement used the current method.

        Returns:
            True if :attr:`method` is :attr:`METHOD`.
        """
        return self.method == self.METHOD

    @property
    def repair_advised(self) -> bool:
        """Tell whether a repair is likely to give a useful video.

        Returns:
            True if at least :attr:`REPAIR_FROM` of the duration is usable.
        """
        return self.opened and self.usable_share >= self.REPAIR_FROM

    def summary(self) -> str:
        """Describe the measurement in one line.

        Returns:
            Text such as ``12:00 min, 98 % decodes, 97 % usable, 3 errors``.
        """
        if not self.opened:
            return f"does not open ({self.note or 'no video stream'})"
        minutes, seconds = divmod(int(round(self.duration)), 60)
        return (f"{minutes}:{seconds:02d} min, {100 * self.decoded_share:.0f} % decodes, "
                f"{100 * self.usable_share:.0f} % usable, {self.errors} decoder error(s)")

    def to_dict(self) -> dict:
        """Return the fields for the manifest.

        Returns:
            Plain dictionary.
        """
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Assessment:
        """Rebuild an assessment from the manifest.

        Args:
            data: Dictionary written by :meth:`to_dict`.

        Returns:
            The assessment; unknown keys are ignored, a record without
            ``method`` was written by mediatools 1.7.0 (method 1).
        """
        names = ("opened", "duration", "decoded", "still", "errors", "note")
        return cls(**{key: data[key] for key in names if key in data},
                   method=int(data.get("method", 1)))
