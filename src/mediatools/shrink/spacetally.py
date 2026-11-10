"""Read, written and freed space of shrinking, for this run and overall."""
from __future__ import annotations

from mediatools.shrink.throughput import ThroughputLog


class SpaceTally:
    """Counts how much was read, written and freed by shrinking.

    The overall progress line counts *source* bytes, i.e. how much was read
    and encoded. The space freed on disk is the source size minus the size of
    the shrunk file, and the counts of a run start at zero after every start,
    restart or ``mediatools reload``. This class keeps both views apart:

    * this run: files replaced by their shrunk version since the start;
    * overall: all encoded files recorded in ``shrink_throughput.json``
      (kept since 1.2.1), i.e. the state before this run plus this run.

    Copies kept with ``--keep-copies`` free no space; they are not counted
    for this run. The overall figure contains them if earlier runs used that
    option, because ``shrink_throughput.json`` does not tell them apart.

    Attributes:
        earlier_src: Source bytes of all encoded files before this run.
        earlier_out: Output bytes of those files.
        run_src: Source bytes of the files replaced in this run.
        run_out: Output bytes of those files.
    """

    def __init__(self, earlier_src: int = 0, earlier_out: int = 0):
        """Start with the overall state before this run.

        Args:
            earlier_src: Source bytes of all encoded files before this run.
            earlier_out: Output bytes of those files.
        """
        self.earlier_src = earlier_src
        self.earlier_out = earlier_out
        self.run_src = 0
        self.run_out = 0

    @classmethod
    def from_throughput(cls, throughput: ThroughputLog) -> SpaceTally:
        """Start from the totals recorded in the throughput log.

        Args:
            throughput: Measured speed and size ratio of earlier runs.

        Returns:
            A tally with these totals as the state before this run.
        """
        return cls(throughput.ratio_src, throughput.ratio_out)

    def add(self, src: int, out: int) -> None:
        """Count one original replaced by its shrunk version.

        Args:
            src: Source size in bytes.
            out: Size of the shrunk file in bytes.
        """
        self.run_src += src
        self.run_out += out

    def overall(self) -> tuple[int, int]:
        """Return the overall totals including this run.

        Returns:
            Source bytes and output bytes.
        """
        return self.earlier_src + self.run_src, self.earlier_out + self.run_out

    @staticmethod
    def part(src: int, out: int) -> str:
        """Format read, written and freed space of one view.

        Args:
            src: Source bytes (read).
            out: Output bytes (written).

        Returns:
            Text such as ``59.3 GiB read -> 9.1 GiB written = 50.2 GiB freed``.
        """
        gib = 1024 ** 3
        return (f"{src / gib:.1f} GiB read -> {out / gib:.1f} GiB written "
                f"= {(src - out) / gib:.1f} GiB freed")

    def line(self) -> str:
        """Build the space line of the progress display.

        Returns:
            Text without line break, this run first, then overall.
        """
        return (f"space  this run: {self.part(self.run_src, self.run_out)} | "
                f"overall: {self.part(*self.overall())}")

    def summary(self) -> list[str]:
        """Build the log lines for the end of a run.

        Returns:
            Two lines: this run and overall, with the output share; none if
            nothing was ever encoded.
        """
        if not self.overall()[0]:
            return []
        lines = []
        for label, (src, out) in (("this run", (self.run_src, self.run_out)),
                                  ("overall since 1.2.1", self.overall())):
            share = f" (output {out / src * 100:.1f} % of the source)" if src else ""
            lines.append(f"space {label}: {self.part(src, out)}{share}")
        return lines
