"""Control of a running job: background mode, stop request, status for attach."""
from __future__ import annotations

import os
import sys
import time

from mediatools.core.datadir import DataDir
from mediatools.core.sleepinhibitor import SleepInhibitor


class RunControl:
    """Lets ``mediatools stop`` ask a running job to end after its current file.

    The running job writes its process ID to ``<data>/state/<name>.pid``.
    ``mediatools stop`` checks that this process is alive and then creates
    ``<data>/state/<name>.stop``; the job notices the file within
    :attr:`POLL_SECONDS`, finishes the current file and ends - exactly like
    the first Ctrl+C, but independent of which window has the keyboard
    focus. Both files are removed when the job ends; a stop file found at
    start (left by a crash) is removed as well. A second job of the same
    name cannot start while the first one is alive (:meth:`start` raises
    :class:`RuntimeError`); a PID file of a crashed job does not block.

    The PID file also names the log file, and the job writes its progress
    lines to ``<data>/state/<name>.status``; ``mediatools attach`` shows
    both. With ``background`` the job moves into the background when the
    real work begins (:meth:`begin_work`, after the confirmation): it forks,
    the terminal gets its prompt back, and the job continues in a new
    session, independent of the terminal - closing the window does not end
    it. Its terminal output goes to ``<data>/state/<name>.console``. The
    optional sleep lock is taken in the process that does the work.

    Attributes:
        name: Job name, e.g. ``shrink``.
        pid_file: Path of the PID file (PID and log file path).
        stop_file: Path of the stop-request file.
        status_file: Path of the progress status file.
        console_file: Terminal output of a job in the background.
        last_poll: Monotonic time of the last check for the stop file.
        background: Move into the background in :meth:`begin_work`.
        inhibitor: Sleep lock held while the work runs, or None.
        log_path: Log file of the job, or None.
    """

    POLL_SECONDS = 2.0

    def __init__(self, name: str, data: DataDir | None = None, background: bool = False,
                 inhibitor: SleepInhibitor | None = None, log_path: str | None = None):
        """Locate the control files.

        Args:
            name: Job name, e.g. ``shrink``.
            data: Data directory; the default one if None.
            background: Move into the background in :meth:`begin_work`.
            inhibitor: Sleep lock held while the work runs, or None.
            log_path: Log file of the job, or None.
        """
        state = (data or DataDir()).sub("state")
        self.name = name
        self.pid_file = os.path.join(state, f"{name}.pid")
        self.stop_file = os.path.join(state, f"{name}.stop")
        self.status_file = os.path.join(state, f"{name}.status")
        self.console_file = os.path.join(state, f"{name}.console")
        self.last_poll = 0.0
        self.background = background
        self.inhibitor = inhibitor
        self.log_path = log_path

    # -- running job -----------------------------------------------------
    def start(self) -> None:
        """Register the current process and drop a stale stop request.

        Raises:
            RuntimeError: If another job of this name is still running.
        """
        other = self.active_pid()
        if other is not None and other != os.getpid():
            raise RuntimeError(f"{self.name} is already running (pid {other}) - "
                               f"watch it with 'mediatools attach', stop it with "
                               f"'mediatools stop'")
        self._remove(self.stop_file)
        self._register()

    def _register(self) -> None:
        """Write PID and log file path of the current process."""
        with open(self.pid_file, "w", encoding="utf-8") as handle:
            handle.write(f"{os.getpid()}\n{self.log_path or ''}\n")

    def begin_work(self) -> None:
        """Enter the work phase: background (if set), then the sleep lock."""
        if self.background:
            self.detach()
        if self.inhibitor is not None:
            self.inhibitor.start()

    def detach(self) -> None:
        """Continue in a background process; the calling process exits.

        The parent prints how to follow and stop the job, then ends at
        once with ``os._exit(0)`` - without cleanup, which belongs to the
        child. The child starts a new session (no controlling terminal, so
        closing the window sends it no SIGHUP), reads from /dev/null,
        writes its terminal output to :attr:`console_file` and registers
        its own PID.
        """
        sys.stdout.flush()
        sys.stderr.flush()
        pid = os.fork()
        if pid:
            print(f"{self.name} continues in the background (pid {pid}); the terminal "
                  f"may be closed.\n  watch: mediatools attach   (Ctrl+C leaves the view)\n"
                  f"  stop:  mediatools stop     (after the current file)\n"
                  f"  log:   {self.log_path or '-'}", flush=True)
            os._exit(0)                          # pylint: disable=protected-access
        os.setsid()
        null = os.open(os.devnull, os.O_RDONLY)
        console = os.open(self.console_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        os.dup2(null, 0)
        os.dup2(console, 1)
        os.dup2(console, 2)
        os.close(null)
        os.close(console)
        self._register()

    def finish(self) -> None:
        """Unregister the current process and release the sleep lock."""
        if self.inhibitor is not None:
            self.inhibitor.stop()
        if self.active_pid() == os.getpid():
            self._remove(self.pid_file)
            self._remove(self.status_file)
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
                pid = int(handle.readline().strip())
        except (FileNotFoundError, ValueError):
            return None
        try:
            os.kill(pid, 0)                      # signal 0: existence check only
        except ProcessLookupError:
            return None
        except PermissionError:
            return pid                           # alive, owned by another user
        return pid

    def active_log(self) -> str | None:
        """Return the log file of the running job.

        Returns:
            Path from the PID file, or None if no job runs or none is named.
        """
        if self.active_pid() is None:
            return None
        try:
            with open(self.pid_file, encoding="utf-8") as handle:
                lines = handle.read().splitlines()
        except FileNotFoundError:
            return None
        return lines[1] if len(lines) > 1 and lines[1] else None

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
