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
        return entry

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
        if declined or (code not in self.CONTINUE and not go_on):
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
            self.add(entry["args"], entry.get("note", ""), front=True)
            print(f"queue: next job not started ({err}) - 'mediatools queue --run' "
                  "tries again", file=sys.stderr)
        return 1
