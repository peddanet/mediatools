"""State of the shrink run for the GUI: process, progress lines, log, space."""
from __future__ import annotations

import os
import re

from mediatools.core.runcontrol import RunControl
from mediatools.core.runstatus import RunStatus
from mediatools.gui.shrinkreading import ShrinkReading
from mediatools.shrink.spacetally import SpaceTally
from mediatools.shrink.throughput import ThroughputLog


class ShrinkMonitor:
    """Reads the files that a shrink run maintains, without touching it.

    The same files serve ``mediatools status`` and ``attach``: the PID file
    (process and log), the status file (progress lines) and
    ``shrink_throughput.json`` (sizes of all runs). Stop and reload are
    requests by file, exactly as the command line makes them.

    Attributes:
        control: Run control of the shrink job.
        throughput_path: Location of ``shrink_throughput.json``, or None for
            the default.
    """

    PERCENT = re.compile(r"\]\s+([0-9.]+) %")

    def __init__(self, control: RunControl | None = None, throughput_path: str | None = None):
        """Prepare the monitor.

        Args:
            control: Run control; the one of ``shrink`` if None.
            throughput_path: ``shrink_throughput.json``; the default if None.
        """
        self.control = control or RunControl("shrink")
        self.throughput_path = throughput_path

    def read(self) -> ShrinkReading:
        """Read the current state.

        Returns:
            The state; ``pid`` is None if no shrink runs.
        """
        pid = self.control.active_pid()
        state = ShrinkReading(pid=pid, report=RunStatus(self.control).lines())
        state.overall = "overall: " + SpaceTally.part(
            *SpaceTally.from_throughput(ThroughputLog(self.throughput_path)).overall())
        if pid is None:
            return state
        state.log_path = self.control.active_log()
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
