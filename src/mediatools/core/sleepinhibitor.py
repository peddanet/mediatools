"""Keep the computer from sleeping while a long job runs (systemd-inhibit)."""
from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess  # nosec B404 - fixed argument list, no shell
import time


class SleepInhibitor:
    """Hold a systemd sleep/idle inhibitor lock for the lifetime of a job.

    Runs ``systemd-inhibit --what=sleep:idle --mode=block tail
    --pid=<own pid> -f /dev/null`` in its own session: the lock lives as
    long as the helper; ``tail --pid`` ends it at the latest when this
    process dies, even after SIGKILL, so no lock is left behind. Ctrl+C in
    the terminal does not reach the helper (own session). If systemd-inhibit
    is missing or refuses the lock, the job runs without it and a warning
    is logged.

    Example:
        >>> lock = SleepInhibitor("mediatools shrink")
        >>> lock.start()
        >>> lock.stop()

    Attributes:
        why: Reason shown by ``systemd-inhibit --list``.
        log: Optional logger.
        proc: The helper process while the lock is held, else None.
    """

    SETTLE_SECONDS = 0.5

    def __init__(self, why: str, log: logging.Logger | None = None,
                 inhibit: str = "systemd-inhibit") -> None:
        """Configure the lock.

        Args:
            why: Reason shown by ``systemd-inhibit --list``.
            log: Optional logger.
            inhibit: Name or path of the systemd-inhibit program.
        """
        self.why = why
        self.log = log
        self.inhibit = inhibit
        self.proc: subprocess.Popen | None = None

    def start(self) -> bool:
        """Take the lock.

        Returns:
            True if the lock is held.
        """
        inhibit, tail = shutil.which(self.inhibit), shutil.which("tail")
        if not inhibit or not tail:
            self._warn("systemd-inhibit or tail not found - sleep is not inhibited")
            return False
        cmd = [inhibit, "--what=sleep:idle", "--who=mediatools", f"--why={self.why}",
               "--mode=block", tail, f"--pid={os.getpid()}", "-f", "/dev/null"]
        # the helper must outlive this method, so no 'with' block
        self.proc = subprocess.Popen(  # nosec B603 pylint: disable=consider-using-with
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, start_new_session=True)
        time.sleep(self.SETTLE_SECONDS)
        if self.proc.poll() is not None:
            reason = ""
            if self.proc.stderr:
                reason = self.proc.stderr.read().decode(errors="replace").strip()
                self.proc.stderr.close()
            self._warn(f"systemd-inhibit refused the lock ({reason or self.proc.returncode})"
                       " - sleep is not inhibited")
            self.proc = None
            return False
        if self.log:
            self.log.info("sleep and idle are inhibited while this run lasts (systemd-inhibit)")
        return True

    def stop(self) -> None:
        """Release the lock (no-op if none is held)."""
        if self.proc is None:
            return
        try:
            os.killpg(self.proc.pid, signal.SIGTERM)     # helper and its tail
        except ProcessLookupError:
            pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        if self.proc.stderr:
            self.proc.stderr.close()
        self.proc = None

    def _warn(self, message: str) -> None:
        """Log a warning if a logger is set.

        Args:
            message: Warning text.
        """
        if self.log:
            self.log.warning("%s", message)
