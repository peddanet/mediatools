"""State of the shrink run for the GUI: process, progress lines, log, space."""
from __future__ import annotations

import os
import re

from mediatools.core.datadir import DataDir
from mediatools.core.runcontrol import RunControl
from mediatools.core.runstatus import RunStatus
from mediatools.gui.shrinkreading import ShrinkReading
from mediatools.shrink.jobregistry import JobRegistry
from mediatools.shrink.ledger import Ledger
from mediatools.shrink.savingslog import SavingsLog
from mediatools.shrink.spacetally import SpaceTally


class ShrinkMonitor:
    """Reads the files that a shrink run maintains, without touching it.

    The same files serve ``mediatools status`` and ``attach``: the PID file
    (process and log), the status file (progress lines) and the shrink logs
    (saved space of the session and of all sessions, :class:`SavingsLog`).
    Stop and reload are requests by file, exactly as the command line makes
    them.

    Attributes:
        control: Run control of the shrink job.
        logs: Reader of the shrink logs (cached between readings).
        ledger: Ledger of finished videos (cached between readings), or None.
    """

    PERCENT = re.compile(r"\]\s+([0-9.]+) %")

    def __init__(self, control: RunControl | None = None, logs: SavingsLog | None = None):
        """Prepare the monitor.

        Args:
            control: Run control; the one of ``shrink`` if None.
            logs: Reader of the shrink logs; the data folder's logs if None.
        """
        self.control = control or RunControl("shrink")
        self.logs = logs or SavingsLog(DataDir().sub("logs"))
        self.ledger: Ledger | None = None

    def read(self) -> ShrinkReading:
        """Read the current state.

        Returns:
            The state; ``pid`` is None if no shrink runs.
        """
        pid = self.control.active_pid()
        state = ShrinkReading(pid=pid, report=RunStatus(self.control).lines())
        if pid is not None:
            state.log_path = self.control.active_log()
        logs = self.logs.logs()
        session = state.log_path or (logs[-1] if logs else None)
        ledger = self.registry().ledger
        if ledger is not None and os.path.exists(ledger.path):
            name = os.path.basename(session)[:-4] if session else None
            line = SpaceTally.from_ledger(ledger, name).line()
        else:
            line = SpaceTally.from_logs(self.logs, session).line()
        state.overall = line.replace("space  session", "space  last session", 1) \
            if pid is None else line
        if pid is None:
            return state
        try:
            with open(self.control.status_file, encoding="utf-8") as handle:
                state.progress = handle.read().splitlines()[:3]
        except FileNotFoundError:
            state.progress = []
        if state.progress:
            match = self.PERCENT.search(state.progress[0])
            state.percent = float(match.group(1)) if match else None
        return state

    @staticmethod
    def job_files() -> list[str]:
        """Return the files the job list is built from.

        Returns:
            Paths of ``jobs.json`` and ``ledger.tsv``.
        """
        datadir = DataDir()
        return [os.path.join(datadir.sub("state"), "jobs.json"),
                os.path.join(datadir.data_dir, "ledger.tsv")]

    def registry(self) -> JobRegistry:
        """Return the remembered shrink jobs with the ledger figures.

        Returns:
            The registry.
        """
        state, ledger = self.job_files()
        if self.ledger is None or self.ledger.path != ledger:
            self.ledger = Ledger(ledger, DataDir().sub("logs"))
        return JobRegistry(state, self.ledger)

    @staticmethod
    def tail(path: str | None, lines: int = 400, chunk: int = 65536) -> list[str]:
        """Return the last lines of a log file.

        Args:
            path: Log file, or None.
            lines: Number of lines to return at most.
            chunk: Bytes read from the end of the file.

        Returns:
            The lines without line ends; empty if the file is missing.
        """
        if not path:
            return []
        try:
            with open(path, "rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - chunk))
                data = handle.read()
        except OSError:
            return []
        text = data.decode("utf-8", errors="replace").splitlines()
        if size > chunk and text:
            text = text[1:]                      # first line may be cut
        return text[-lines:]

    def stop(self) -> str:
        """Ask the running shrink to stop after its current file.

        Returns:
            Message for the GUI.
        """
        pid = self.control.request_stop()
        if pid is None:
            return "no shrink run is active - nothing to stop"
        return f"stop requested: shrink (pid {pid}) finishes its current file and ends"

    def reload(self) -> str:
        """Ask the running shrink to switch to the installed release.

        Returns:
            Message for the GUI.
        """
        pid = self.control.request_restart()
        if pid is None:
            return "no shrink run is active - nothing to reload"
        return (f"reload requested: shrink (pid {pid}) switches to the installed release "
                f"after its current file")
