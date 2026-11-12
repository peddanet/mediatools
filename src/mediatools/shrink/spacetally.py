"""Read, written and freed space of shrinking, for this session and overall."""
from __future__ import annotations

from mediatools.shrink.savingslog import SavingsLog


class SpaceTally:
    """Counts how much was read, written and freed by shrinking.

    The overall progress line counts *source* bytes, i.e. how much was read
    and encoded. The space freed on disk is the source size minus the size of
    the shrunk file. This class keeps two views, both taken from the result
    tables of the shrink logs (:class:`SavingsLog`) when the run starts and
    then counted on per replaced file:

    * this session: everything shrunk since the session started. A session
      is one log; ``mediatools reload`` continues the log, so the session
      (unlike the process) survives a release switch;
    * overall: all shrink logs, labelled with the date of the first shrunk
      file (``since 2026-10-20``).

    Copies kept with ``--keep-copies`` free no space and are not counted.

    Attributes:
        earlier_src: Source bytes of all logs when this process started.
        earlier_out: Output bytes of all logs when this process started.
        session_src: Source bytes of this session before this process.
        session_out: Output bytes of this session before this process.
        run_src: Source bytes replaced by this process.
        run_out: Output bytes of those files.
        since: Label of the overall view, e.g. ``since 2026-10-20``.
        started: Start of the session, e.g. ``2026-11-12 04:56``.
    """

    def __init__(self, earlier: tuple[int, int] = (0, 0), session: tuple[int, int] = (0, 0),
                 since: str = "overall", started: str = ""):
        """Start with the state before this process.

        Args:
            earlier: Source and output bytes of all logs so far.
            session: Source and output bytes of this session so far.
            since: Label of the overall view.
            started: Start of the session; empty if unknown.
        """
        self.earlier_src, self.earlier_out = earlier
        self.session_src, self.session_out = session
        self.run_src = 0
        self.run_out = 0
        self.since = since
        self.started = started

    @classmethod
    def from_logs(cls, logs: SavingsLog, current: str | None) -> SpaceTally:
        """Start from the result tables of the shrink logs.

        Args:
            logs: Reader of the shrink logs.
            current: Log of this session, or None.

        Returns:
            A tally with all logs as overall state and the current log as
            session state.
        """
        total = logs.total()
        session = logs.session(current) if current else None
        return cls((total.src, total.out),
                   (session.src, session.out) if session else (0, 0),
                   total.name if total.files else "overall",
                   f"{session.start:%Y-%m-%d %H:%M}"
                   if session and logs.started(current or "") else "")

    def add(self, src: int, out: int) -> None:
        """Count one original replaced by its shrunk version.

        Args:
            src: Source size in bytes.
            out: Size of the shrunk file in bytes.
        """
        self.run_src += src
        self.run_out += out

    def session(self) -> tuple[int, int]:
        """Return the totals of this session.

        Returns:
            Source bytes and output bytes.
        """
        return self.session_src + self.run_src, self.session_out + self.run_out

    def overall(self) -> tuple[int, int]:
        """Return the totals of all sessions including this one.

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

    def session_label(self) -> str:
        """Return the label of the session view.

        Returns:
            ``session since 2026-11-12 04:56`` or ``session``.
        """
        return f"session since {self.started}" if self.started else "session"

    def line(self) -> str:
        """Build the space line of the progress display.

        Returns:
            Text without line break, this session first, then overall.
        """
        return (f"space  {self.session_label()}: {self.part(*self.session())} | "
                f"{self.since}: {self.part(*self.overall())}")

    def summary(self) -> list[str]:
        """Build the log lines for the end of a run.

        Returns:
            Two lines: this session and overall, with the output share;
            none if nothing was ever shrunk.
        """
        if not self.overall()[0]:
            return []
        lines = []
        for label, (src, out) in ((self.session_label(), self.session()),
                                  (self.since, self.overall())):
            share = f" (output {out / src * 100:.1f} % of the source)" if src else ""
            lines.append(f"space {label}: {self.part(src, out)}{share}")
        return lines
