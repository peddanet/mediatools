"""Replace a running mediatools process by the newly installed release."""
from __future__ import annotations

import os
import subprocess
import sys


class SelfRestart:
    """Restarts the current command with the installed release, in the same process.

    Used by ``mediatools shrink`` when ``mediatools reload`` (or ``mediatools
    update``) asks a running job to switch to a newly installed release.
    The switch happens only between two files: the job finishes its current
    file, closes its result table and then calls :meth:`execute`, which
    replaces the process image with ``python -m mediatools`` of the same
    interpreter (``os.execv``). The process keeps its PID, session, terminal
    or console file and environment, so ``mediatools attach`` and ``stop``
    keep working and the sleep lock (bound to the PID) is taken over.

    The new process runs the same command line plus ``--foreground`` (it is
    already in the background), ``-y`` (the user confirmed the run) and
    ``--continue-log`` (it appends to the same log file). It plans again;
    every file finished so far is recorded in the databases, so it continues
    with the files left. Before the switch, :meth:`installed_version` starts
    the installed release once with ``--version``; a release that does not
    answer is not switched to.

    Attributes:
        argv: Command-line arguments of the running command (without the
            program name).
        log_path: Log file the new process continues.
        python: Interpreter of the installed release.
    """

    TIMEOUT = 60.0
    DROP = ("--foreground", "-y", "--yes")

    def __init__(self, argv: list[str], log_path: str, python: str | None = None) -> None:
        """Store what the new process needs.

        Args:
            argv: Arguments of the running command, without the program name.
            log_path: Log file to continue.
            python: Interpreter; the running one if None.
        """
        self.argv = list(argv)
        self.log_path = log_path
        self.python = python or sys.executable

    def command(self) -> list[str]:
        """Build the command line of the new process.

        Returns:
            Argument vector for :func:`os.execv` (element 0 is the program).
        """
        args: list[str] = []
        skip = False
        for arg in self.argv:
            if skip:
                skip = False
            elif arg == "--continue-log":
                skip = True
            elif arg not in self.DROP and not arg.startswith("--continue-log="):
                args.append(arg)
        return [self.python, "-m", "mediatools", *args, "--foreground", "-y",
                "--continue-log", self.log_path]

    def installed_version(self) -> str | None:
        """Ask the installed release for its version.

        Returns:
            Version string, e.g. ``1.2.11``, or None if it does not start or
            answers unexpectedly.
        """
        try:
            res = subprocess.run([self.python, "-m", "mediatools", "--version"],
                                 capture_output=True, text=True, check=False,
                                 timeout=self.TIMEOUT)
        except (OSError, subprocess.TimeoutExpired):
            return None
        words = res.stdout.split()
        if res.returncode != 0 or len(words) < 2 or words[0] != "mediatools":
            return None
        return words[1]

    def execute(self) -> None:
        """Replace the current process by the new one; returns only on failure.

        Raises:
            OSError: If the program cannot be started.
        """
        sys.stdout.flush()
        sys.stderr.flush()
        cmd = self.command()
        os.execv(cmd[0], cmd)
