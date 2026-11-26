"""Shrink jobs waiting to run one after the other."""
from __future__ import annotations

import json
import os
import shlex
import sys
import time

from mediatools.core.datadir import DataDir
from mediatools.core.runlog import RunLog


class ShrinkQueue:
    """A list of ``shrink`` command lines that run one after the other.

    The queue is a JSON file in the state folder (mode 600; it holds paths
    of the collection). Each entry is the argument list of one ``mediatools
    shrink`` run (folder first, then options) with a note and the time it
    was added. Entries are added by ``mediatools queue --add`` or by
    ``mediatools estimate --suggest N --queue`` and run in list order.

    **How it runs.** A shrink job that ends normally - done, or done with
    failed files (exit codes 0 and 2), not declined - takes the first entry
    and replaces its own process with it (:meth:`continue_after`,
    ``os.execv``), so the next job runs in the same background process.
    ``mediatools stop`` (exit code 1) ends the current job and leaves the
    queue as it is: it *pauses*. ``mediatools queue --run`` starts the
    first entry when no shrink runs.

    **Paused jobs.** A started job is taken off the list,
    but kept in ``<queue file>.current`` until it ends. If it does not end
    normally - stopped by the user (``mediatools stop``, with or without
    ``--now``), or ended by an error - it goes back to the list as first
    entry, marked *paused* (after *Stop, then next*: second, behind the
    job that starts now). The user decides: *Continue* goes on where it
    stopped (the directory databases skip the finished files), *Remove*
    drops it. A job left in the record by a crash is put back the same way
    by :meth:`recover`, as soon as no shrink runs.

    Attributes:
        path: Location of the JSON file.
        python: Interpreter for the next job (the running one).
    """

    FILE = "shrink_queue.json"
    CONTINUE = (0, 2)

    def __init__(self, path: str, python: str | None = None) -> None:
        """Bind the queue to its file.

        Args:
            path: Location of the JSON file.
            python: Interpreter for the next job; the running one if None.
        """
        self.path = path
        self.python = python or sys.executable

    @classmethod
    def default(cls) -> ShrinkQueue:
        """Return the queue of this user.

        Returns:
            ``<state>/shrink_queue.json``.
        """
        return cls(os.path.join(DataDir().sub("state"), cls.FILE))

    def entries(self) -> list[dict]:
        """Read the entries.

        Returns:
            Entries with ``args`` (list), ``note`` and ``added``; empty if
            the file is missing or cannot be read.
        """
        try:
            with open(self.path, encoding="utf-8", errors="surrogateescape") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return []
        return [entry for entry in data if isinstance(entry, dict)
                and isinstance(entry.get("args"), list)] if isinstance(data, list) else []

    def _save(self, entries: list[dict]) -> None:
        """Write the entries atomically with mode 600.

        Args:
            entries: New content.
        """
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = f"{self.path}.{os.getpid()}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", errors="surrogateescape") as handle:
            json.dump(entries, handle, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    def add(self, args: list[str], note: str = "", front: bool = False) -> int:
        """Append (or put first) one job.

        Args:
            args: Arguments of ``mediatools shrink`` (folder first).
            note: Free text shown in the list.
            front: Put it first instead of last.

        Returns:
            Its position (1 = next).
        """
        entries = self.entries()
        entry = {"args": list(args), "note": note,
                 "added": time.strftime("%Y-%m-%d %H:%M")}
        if front:
            entries.insert(0, entry)
        else:
            entries.append(entry)
        self._save(entries)
        return 1 if front else len(entries)

    def replace(self, number: int, args: list[str], note: str | None = None) -> None:
        """Change the arguments (and note) of one job, keeping its place.

        Args:
            number: Position, 1 = next.
            args: New arguments of ``mediatools shrink``.
            note: New note, or None to keep it.

        Raises:
            ValueError: If there is no job at that position.
        """
        entries = self.entries()
        self._check(number, entries)
        entries[number - 1]["args"] = list(args)
        if note is not None:
            entries[number - 1]["note"] = note
        self._save(entries)

    def remove(self, number: int) -> dict:
        """Remove one job.

        Args:
            number: Position, 1 = next.

        Returns:
            The removed entry.

        Raises:
            ValueError: If there is no job at that position.
        """
        entries = self.entries()
        self._check(number, entries)
        entry = entries.pop(number - 1)
        self._save(entries)
        return entry

    def move(self, number: int, to: int) -> None:
        """Move a job to another position.

        Args:
            number: Current position, 1 = next.
            to: New position (clipped to the list).

        Raises:
            ValueError: If there is no job at ``number``.
        """
        entries = self.entries()
        self._check(number, entries)
        entry = entries.pop(number - 1)
        entries.insert(min(max(to, 1), len(entries) + 1) - 1, entry)
        self._save(entries)

    @staticmethod
    def _check(number: int, entries: list[dict]) -> None:
        """Raise if a position does not exist.

        Args:
            number: Position, 1 = next.
            entries: The list.

        Raises:
            ValueError: If the position is not in the list.
        """
        if not 1 <= number <= len(entries):
            raise ValueError(f"no job number {number} in the queue ({len(entries)} job(s))")

    def clear(self) -> int:
        """Remove every job.

        Returns:
            Number of jobs removed.
        """
        count = len(self.entries())
        self._save([])
        return count

    def pop(self) -> dict | None:
        """Take the next job off the queue.

        Returns:
            The entry, or None if the queue is empty.
        """
        entries = self.entries()
        if not entries:
            return None
        entry = entries.pop(0)
        self._save(entries)
        self._write_current(entry)
        return entry

    @property
    def current_file(self) -> str:
        """Record of the job that runs now (taken off the list).

        Returns:
            ``<queue file>.current``.
        """
        return self.path + ".current"

    def _write_current(self, entry: dict) -> None:
        """Remember the started job atomically (mode 600).

        The process ID is stored with it: the job runs in this process
        (``queue --run`` and the switch to the next job keep the PID), so
        :meth:`recover` can tell a running job from one left by a crash.

        Args:
            entry: The entry taken off the list.
        """
        tmp = f"{self.current_file}.{os.getpid()}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", errors="surrogateescape") as handle:
            json.dump({**entry, "pid": os.getpid()}, handle, ensure_ascii=False)
        os.replace(tmp, self.current_file)

    def take_current(self) -> dict | None:
        """Read and forget the record of the started job.

        Returns:
            The entry, or None if there is none (or it cannot be read).
        """
        try:
            with open(self.current_file, encoding="utf-8", errors="surrogateescape") as handle:
                entry = json.load(handle)
        except (OSError, ValueError):
            entry = None
        if os.path.exists(self.current_file):
            os.remove(self.current_file)
        return entry if isinstance(entry, dict) and isinstance(entry.get("args"), list) \
            else None

    def put_back(self, entry: dict, position: int = 1) -> int:
        """Return a job that did not end normally to the list, marked paused.

        Args:
            entry: The entry of the job.
            position: Place in the list, 1 = next.

        Returns:
            Its position.
        """
        entries = self.entries()
        paused = {key: value for key, value in entry.items() if key != "pid"}
        paused["paused"] = time.strftime("%Y-%m-%d %H:%M")
        place = max(0, min(position - 1, len(entries)))
        entries.insert(place, paused)
        self._save(entries)
        return place + 1

    def recover(self, running: int | None = None) -> bool:
        """Put back a job whose record a crash left behind.

        The record is left alone while the process that took the job is
        alive (it is about to start the job), and while a shrink runs:
        the job moves into the background with a new process ID
        (:meth:`RunControl.detach`), so the ID in the record belongs to the
        ended starter, although its job still runs.

        Args:
            running: PID of the running shrink (:meth:`RunControl.active_pid`),
                or None if none runs.

        Returns:
            True if a job was put back.
        """
        if running is not None:
            return False
        try:
            with open(self.current_file, encoding="utf-8", errors="surrogateescape") as handle:
                pid = json.load(handle).get("pid")
        except (OSError, ValueError, AttributeError):
            pid = None
        if not os.path.exists(self.current_file) or self._alive(pid):
            return False
        entry = self.take_current()
        if entry is None:
            return False
        self.put_back(entry)
        return True

    @staticmethod
    def _alive(pid: object) -> bool:
        """Tell whether a process ID belongs to a living process.

        Args:
            pid: Process ID from the record (anything else counts as dead).

        Returns:
            True if the process exists.
        """
        if not isinstance(pid, int) or pid <= 0:
            return False
        try:
            os.kill(pid, 0)                      # signal 0: existence check only
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def lines(self) -> list[str]:
        """Describe the queue.

        Returns:
            One line per job, ``1. mediatools shrink ...  (note)``, or a
            line saying it is empty.
        """
        entries = self.entries()
        if not entries:
            return ["queue: empty"]
        return [f"{number}. mediatools shrink {shlex.join(entry['args'])}"
                + (f"   (paused {entry['paused']})" if entry.get("paused") else "")
                + (f"   ({entry['note']})" if entry.get("note") else "")
                for number, entry in enumerate(entries, 1)]

    def summary(self) -> list[str]:
        """Return one status line about the queue, or none if it is empty.

        Returns:
            ``["queue: N job(s) waiting - 'mediatools queue' lists them"]``
            or ``[]``.
        """
        count = len(self.entries())
        return [f"queue:     {count} job(s) waiting - 'mediatools queue' lists them"] \
            if count else []

    def command(self, args: list[str]) -> list[str]:
        """Build the command line of the next job.

        Args:
            args: Arguments of ``mediatools shrink`` from the queue.

        Returns:
            Argument vector for :func:`os.execv`: the job already runs in
            the background, so ``--foreground``; it was confirmed when it
            was queued, so ``-y``.
        """
        return [self.python, "-m", "mediatools", "shrink", *args, "--foreground", "-y"]

    @property
    def next_file(self) -> str:
        """Marker: continue with the next job after the coming stop.

        Returns:
            ``<queue file>.next``.
        """
        return self.path + ".next"

    def next_after_stop(self, wanted: bool = True) -> None:
        """Ask for (or withdraw) the start of the next job after a user's stop.

        Args:
            wanted: True to set the marker, False to remove it.
        """
        if wanted:
            with open(self.next_file, "w", encoding="utf-8") as handle:
                handle.write("continue after the stop\n")
        elif os.path.exists(self.next_file):
            os.remove(self.next_file)

    def continue_after(self, code: int, declined: bool, run: RunLog,
                       stopped: bool = False) -> int:
        """Start the next queued job in this process, if the last one ended normally.

        A stop by the user ends the queue - unless *Stop, then next* asked
        for the next job (:meth:`next_after_stop`); the marker is used once.
        A job from the queue that did not end normally goes back to the
        list, marked paused (:meth:`put_back`).

        Args:
            code: Exit code of the job that ended.
            declined: True if the user declined to start it.
            run: :class:`RunLog` of the job that ended (closed before the
                switch).
            stopped: True if the job ended because the user stopped it.

        Returns:
            ``code`` if nothing follows; 1 only if the next job could not be
            started (it is put back first).
        """
        go_on = stopped and os.path.exists(self.next_file)
        self.next_after_stop(False)
        current = self.take_current()
        if current is not None and (stopped or code not in self.CONTINUE):
            waiting = bool(self.entries())
            self.put_back(current, 2 if go_on and waiting else 1)
            go_on = go_on and waiting        # never restart the job just stopped
        if declined or (not go_on and (stopped or code not in self.CONTINUE)):
            return code
        entry = self.pop()
        if entry is None:
            return code
        cmd = self.command(entry["args"])
        run.logger.info("queue: starting the next job: mediatools shrink %s",
                        shlex.join(entry["args"]))
        run.close()
        sys.stdout.flush()
        sys.stderr.flush()
        try:
            os.execv(cmd[0], cmd)
        except OSError as err:
            self.take_current()
            self.add(entry["args"], entry.get("note", ""), front=True)
            print(f"queue: next job not started ({err}) - 'mediatools queue --run' "
                  "tries again", file=sys.stderr)
        return 1
