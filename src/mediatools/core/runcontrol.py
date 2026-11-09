"""Control of a running job from another terminal (stop request)."""
from __future__ import annotations

import os
import time

from mediatools.core.datadir import DataDir


class RunControl:
    """Lets ``mediatools stop`` ask a running job to end after its current file.

    The running job writes its process ID to ``<data>/state/<name>.pid``.
    ``mediatools stop`` checks that this process is alive and then creates
    ``<data>/state/<name>.stop``; the job notices the file within
    :attr:`POLL_SECONDS`, finishes the current file and ends - exactly like
    the first Ctrl+C, but independent of which window has the keyboard
    focus. Both files are removed when the job ends; a stop file found at
    start (left by a crash) is removed as well.

    Attributes:
        name: Job name, e.g. ``shrink``.
        pid_file: Path of the PID file.
        stop_file: Path of the stop-request file.
        last_poll: Monotonic time of the last check for the stop file.
    """

    POLL_SECONDS = 2.0

    def __init__(self, name: str, data: DataDir | None = None):
        """Locate the control files.

        Args:
            name: Job name, e.g. ``shrink``.
            data: Data directory; the default one if None.
        """
        state = (data or DataDir()).sub("state")
        self.name = name
        self.pid_file = os.path.join(state, f"{name}.pid")
        self.stop_file = os.path.join(state, f"{name}.stop")
        self.last_poll = 0.0

    # -- running job -----------------------------------------------------
    def start(self) -> None:
        """Register the current process and drop a stale stop request."""
        self._remove(self.stop_file)
        with open(self.pid_file, "w", encoding="utf-8") as handle:
            handle.write(f"{os.getpid()}\n")

    def finish(self) -> None:
        """Unregister the current process."""
        if self.active_pid() == os.getpid():
            self._remove(self.pid_file)
        self._remove(self.stop_file)

    def stop_requested(self, force: bool = False) -> bool:
        """Tell whether a stop was requested, checking at most every POLL_SECONDS.

        Args:
            force: Check now, ignoring the poll interval.

        Returns:
            True if the stop file exists.
        """
        now = time.monotonic()
        if not force and now - self.last_poll < self.POLL_SECONDS:
            return False
        self.last_poll = now
        return os.path.exists(self.stop_file)

    # -- other terminal --------------------------------------------------
    def active_pid(self) -> int | None:
        """Return the process ID of the running job, if it is alive.

        Returns:
            PID, or None if no job of this name runs.
        """
        try:
            with open(self.pid_file, encoding="utf-8") as handle:
                pid = int(handle.read().strip())
        except (FileNotFoundError, ValueError):
            return None
        try:
            os.kill(pid, 0)                      # signal 0: existence check only
        except ProcessLookupError:
            return None
        except PermissionError:
            return pid                           # alive, owned by another user
        return pid

    def request_stop(self) -> int | None:
        """Ask the running job to stop after its current file.

        Returns:
            PID of the job that was asked, or None if none runs (then no
            stop file is left behind that would stop the next run).
        """
        pid = self.active_pid()
        if pid is None:
            return None
        with open(self.stop_file, "w", encoding="utf-8") as handle:
            handle.write(f"stop requested at {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        return pid

    @staticmethod
    def _remove(path: str) -> None:
        """Delete a file if it exists.

        Args:
            path: File to delete.
        """
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
