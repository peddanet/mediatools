"""State of a running job for ``mediatools status``: process, versions, log."""
from __future__ import annotations

import os
import re

from mediatools import __version__
from mediatools.core.runcontrol import RunControl


class RunStatus:
    """Collects what is known about a running job and how to bring it up to date.

    The version a process runs with is the one it wrote into its log when it
    started (``mediatools X (pid N)``). After a release switch the new
    process appends a further such line to the same log, so the last line
    counts. The installed version is the version of this code, because
    ``mediatools status`` itself is started from the installation.

    Attributes:
        control: Run control of the job (PID file, request files).
        installed: Installed release, e.g. ``1.2.15``.
    """

    VERSION_LINE = re.compile(r"^(\S+ \S+) INFO +mediatools (\d\S*)(?: .*)? \(pid (\d+)\)\s*$")
    RELOAD_SINCE = (1, 2, 10)

    def __init__(self, control: RunControl, installed: str = __version__) -> None:
        """Remember the job and the installed release.

        Args:
            control: Run control of the job.
            installed: Installed release.
        """
        self.control = control
        self.installed = installed

    @staticmethod
    def version_tuple(version: str) -> tuple[int, ...]:
        """Turn ``1.2.10`` into ``(1, 2, 10)`` for comparisons.

        Args:
            version: Version string.

        Returns:
            Numeric parts; non-numeric parts end the tuple.
        """
        parts = []
        for part in version.split("."):
            if not part.isdigit():
                break
            parts.append(int(part))
        return tuple(parts)

    def running_version(self, log: str | None) -> tuple[str, str] | None:
        """Read the version the job runs with from its log.

        Args:
            log: Log file of the job.

        Returns:
            ``(version, start time)`` of the last version line, or None if the
            log is missing or has none.
        """
        found = None
        try:
            with open(log or "", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    match = self.VERSION_LINE.match(line)
                    if match:
                        found = (match.group(2), match.group(1).split(",")[0])
        except OSError:
            return None
        return found

    def advice(self, running: str | None) -> str:
        """Say whether the running process is current and how to change it.

        Args:
            running: Version of the process, or None if unknown.

        Returns:
            One line of advice.
        """
        if running is None:
            return "unknown (no version line in the log)"
        have, want = self.version_tuple(running), self.version_tuple(self.installed)
        if have == want:
            return "up to date"
        if have > want:
            return "the running release is newer than the installed one"
        if have < self.RELOAD_SINCE:
            return (f"{running} cannot switch releases: 'mediatools stop', wait for the end, "
                    f"start the shrink again")
        return "'mediatools reload' switches to the installed release after the current file"

    def lines(self) -> list[str]:
        """Return the report printed by ``mediatools status``.

        Returns:
            Text lines; the first one says whether the job runs.
        """
        pid = self.control.active_pid()
        if pid is None:
            return [f"{self.control.name}: not running",
                    f"installed: {self.installed}"]
        log = self.control.active_log()
        found = self.running_version(log)
        running = found[0] if found else None
        out = [f"{self.control.name}: running (pid {pid})",
               f"running:   {running or '?'}" + (f"  (started {found[1]})" if found else ""),
               f"installed: {self.installed}",
               f"status:    {self.advice(running)}",
               f"log:       {log or '-'}"]
        if os.path.exists(self.control.restart_file):
            out.append("pending:   reload requested, takes effect after the current file")
        if os.path.exists(self.control.stop_file):
            out.append("pending:   stop requested, the run ends after the current file")
        return out
