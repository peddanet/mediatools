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
    """

    MIN_DECODED = 0.10
    MAX_STILL = 0.90
    REPAIR_FROM = 0.50

    opened: bool
    duration: float = 0.0
    decoded: float = 0.0
    still: float = 0.0
    errors: int = 0
    note: str = ""

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
            The assessment; unknown keys are ignored.
        """
        names = ("opened", "duration", "decoded", "still", "errors", "note")
        return cls(**{key: data[key] for key in names if key in data})
