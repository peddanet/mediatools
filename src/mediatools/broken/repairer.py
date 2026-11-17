"""Re-encoding the usable part of a damaged video."""
from __future__ import annotations

import os

from mediatools.shrink.encoder import HevcEncoder


class RepairEncoder(HevcEncoder):
    """Encodes what can be decoded of a damaged video into a clean HEVC file.

    The input is read with error tolerance (``-err_detect ignore_err``,
    ``-fflags +discardcorrupt``): damaged packets are dropped and decoding
    goes on as far as the data reaches. The result is HEVC with the same
    quality settings as ``shrink``, so a repaired video is shrunk at the
    same time.

    Validation differs from :class:`HevcEncoder`: the output is shorter
    than the damaged input by design, so size and duration are not
    compared. It must have a video stream and - with a decode check - decode
    completely without a single error.
    """

    TOLERANT_INPUT = ["-err_detect", "ignore_err", "-fflags", "+discardcorrupt"]

    def clone(self) -> RepairEncoder:
        """Return an encoder with the same settings.

        Returns:
            A new repair encoder.
        """
        return RepairEncoder(self.crf, self.threads, self.probe, self.log, self.ffmpeg,
                             self.niceness, self.decode, self.preset)

    def command(self, src: str, dst: str) -> list[str]:
        """Build the ffmpeg command with error-tolerant input options.

        Args:
            src: Damaged video.
            dst: Output file.

        Returns:
            Argument list for :func:`subprocess.run`.
        """
        cmd = super().command(src, dst)
        at = cmd.index("-i")
        return cmd[:at] + self.TOLERANT_INPUT + cmd[at:]

    def validate(self, src: str, dst: str) -> bool:
        """Check that the output has video and decodes without errors.

        Args:
            src: Damaged input.
            dst: Repaired output.

        Returns:
            True if the output is usable.
        """
        if not self.probe.has_video(dst):
            self._error("repaired video has no video stream: %s", src)
            return False
        if self.decode is not None:
            limit = max(self.VERIFY_MIN, (self.probe.duration(dst) or 0.0) * self.VERIFY_FACTOR)
            problem = self.decode.verify(dst, limit, self.niceness)
            if problem is not None:
                self._error("repaired video failed the full decode (%s): %s", problem, src)
                return False
        return True

    @staticmethod
    def output_for(held: str) -> str:
        """Return the work file of a repair next to the damaged video.

        Args:
            held: Damaged video inside ``_broken``.

        Returns:
            ``<folder>/repaired-<name>``; the extension, and with it the
            container, stays the same.
        """
        return os.path.join(os.path.dirname(held), "repaired-" + os.path.basename(held))
