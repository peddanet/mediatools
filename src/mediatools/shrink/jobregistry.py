"""Remembered shrink jobs with their settings and progress."""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from mediatools.shrink.jobspec import JobSpec
from mediatools.shrink.ledger import Ledger
from mediatools.shrink.savingslog import SavingsLog


@dataclass
class JobEntry:
    """A remembered job.

    Attributes:
        spec: Root and filters.
        settings: Settings of the last run: ``crf``, ``preset``, ``order``,
            ``jobs``, ``threads``.
        last: Time of the last activity, ``YYYY-MM-DD HH:MM``.
        todo_bytes: Source bytes still to encode (as of the last plan, minus
            the files finished since); -1 if not planned since 1.6.0 (job
            taken from an old log).
        todo_files: Files still to encode; -1 if unknown.
        read: Source bytes encoded so far (from the ledger).
        done_files: Files encoded so far (from the ledger).
    """

    spec: JobSpec
    settings: dict[str, object] = field(default_factory=dict)
    last: str = ""
    todo_bytes: int = 0
    todo_files: int = 0
    read: int = 0
    done_files: int = 0

    @property
    def planned(self) -> bool:
        """True if the remainder is known (planned by 1.6.0 or later)."""
        return self.todo_bytes >= 0

    @property
    def fraction(self) -> float:
        """Share of the job that is done (0..1); 0 if the remainder is unknown."""
        whole = self.read + self.todo_bytes
        return self.read / whole if whole and self.planned else 0.0

    def argv(self) -> list[str]:
        """Return the arguments of ``mediatools`` that continue this job.

        Returns:
            ``["shrink", root, --crf ..., filters ...]``.
        """
        args = ["shrink", self.spec.root]
        crf = self.settings.get("crf")
        if crf not in (None, 28):
            args += ["--crf", str(crf)]
        preset = self.settings.get("preset")
        if preset not in (None, "medium"):
            args += ["--preset", str(preset)]
        order = self.settings.get("order")
        if order not in (None, "largest"):
            args += ["--order", str(order)]
        jobs = self.settings.get("jobs")
        if jobs not in (None, 1):
            args += ["--jobs", str(jobs)]
        threads = self.settings.get("threads")
        if threads:
            args += ["--threads", str(threads)]
        return args + self.spec.filters()

    def gauge(self, width: int = 20) -> str:
        """Return a text progress bar.

        Args:
            width: Number of cells.

        Returns:
            Text such as ``███████░░░░░░░░░░░░░``.
        """
        if not self.planned:
            return "?" * width
        filled = int(round(self.fraction * width))
        return "█" * filled + "░" * (width - filled)

    def progress(self) -> str:
        """Return the progress as text.

        Returns:
            E.g. ``37.2 %``, or ``not planned yet`` for a job from an old log.
        """
        return f"{self.fraction * 100:5.1f} %" if self.planned else "not planned yet"


class JobRegistry:
    """``<state>/jobs.json``: the shrink jobs run so far.

    A job is entered when a run has planned it, with its settings and what
    is still to encode, and updated after every finished video, so the list
    shows the progress of a running job as well. The figures already done
    come from the :class:`Ledger` when the list is read.

    Jobs run before the registry existed are taken once from the shrink
    logs (:meth:`backfill`): each log names its root and order, logs since
    1.6.0 the whole job as JSON. Their remainder is unknown until the job
    runs again.

    Attributes:
        path: Location of the JSON file.
        ledger: Source of the figures already done, or None.
        logs: Folder of the shrink logs for :meth:`backfill`, or None.
    """

    JOB_LINE = re.compile(r"shrink job: (\{.*\})\s*$")
    ROOT_LINE = re.compile(r"shrink root: (.+?)  mode: .*?  order: (\S+)")
    META = "_meta"

    def __init__(self, path: str, ledger: Ledger | None = None, logs: str | None = None):
        """Bind the registry to its file.

        Args:
            path: Location of ``jobs.json``.
            ledger: Ledger for the figures already done; None leaves them 0.
            logs: Folder of the shrink logs; None takes no jobs from logs.
        """
        self.path = path
        self.ledger = ledger
        self.logs = logs

    def _load(self) -> dict[str, dict]:
        """Read the file.

        Returns:
            Raw entries by job key; empty if missing or damaged.
        """
        try:
            text = Path(self.path).read_text(encoding="utf-8")
            data = json.loads(text)
        except (OSError, ValueError):
            data = None
        return data if isinstance(data, dict) else {}

    def _save(self, data: dict[str, dict]) -> None:
        """Write the file atomically.

        Args:
            data: Raw entries by job key.
        """
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=1, sort_keys=True)
        os.replace(tmp, self.path)

    @classmethod
    def from_log(cls, path: str) -> tuple[JobSpec, dict[str, object]] | None:
        """Return the job of a shrink log (its last start line).

        Args:
            path: Shrink log.

        Returns:
            Job and settings, or None if the log names no job.
        """
        found: tuple[JobSpec, dict[str, object]] | None = None
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    match = cls.JOB_LINE.search(line)
                    if match:
                        try:
                            data = json.loads(match.group(1))
                            found = (JobSpec.from_dict(data),
                                     {key: data.get(key) for key in
                                      ("crf", "preset", "order", "jobs", "threads")
                                      if key in data})
                        except (ValueError, KeyError, TypeError):
                            continue
                        continue
                    match = cls.ROOT_LINE.search(line)
                    if match:
                        found = (JobSpec(match.group(1)), {"order": match.group(2)})
        except OSError:
            return None
        return found

    def backfill(self) -> int:
        """Take the jobs of the shrink logs into the registry, once.

        Jobs already in the registry are kept as they are.

        Returns:
            Number of jobs added.
        """
        data = self._load()
        if self.logs is None or data.get(self.META, {}).get("backfilled"):
            return 0
        added = 0
        for path in SavingsLog(self.logs).logs():
            job = self.from_log(path)
            started = SavingsLog.started(os.path.basename(path))
            if job is None or started is None:
                continue
            spec, settings = job
            entry = data.get(spec.key)
            when = f"{started:%Y-%m-%d %H:%M}"
            if entry is None:
                data[spec.key] = {"spec": spec.as_dict(), "settings": settings, "last": when,
                                  "todo_bytes": -1, "todo_files": -1, "source": "log"}
                added += 1
            elif entry.get("source") == "log":
                entry.update({"settings": settings, "last": max(when, entry["last"])})
        data[self.META] = {"backfilled": True}
        self._save(data)
        return added

    def planned(self, spec: JobSpec, settings: dict[str, object], todo_bytes: int,
                todo_files: int) -> None:
        """Enter or update a job after its plan.

        Args:
            spec: Root and filters.
            settings: ``crf``, ``order``, ``jobs`` of this run.
            todo_bytes: Source bytes to encode.
            todo_files: Files to encode.
        """
        self.backfill()
        data = self._load()
        data[spec.key] = {"spec": spec.as_dict(), "settings": settings,
                          "last": time.strftime("%Y-%m-%d %H:%M"),
                          "todo_bytes": todo_bytes, "todo_files": todo_files}
        self._save(data)

    def finished_file(self, spec: JobSpec, size: int) -> None:
        """Take one finished (or skipped) video off the job's remainder.

        Args:
            spec: The running job.
            size: Its source size in bytes.
        """
        data = self._load()
        entry = data.get(spec.key)
        if entry is None or int(entry.get("todo_bytes", 0)) < 0:
            return
        entry["todo_bytes"] = max(0, int(entry.get("todo_bytes", 0)) - size)
        entry["todo_files"] = max(0, int(entry.get("todo_files", 0)) - 1)
        entry["last"] = time.strftime("%Y-%m-%d %H:%M")
        self._save(data)

    def entries(self) -> list[JobEntry]:
        """Return the jobs, the most recently active first.

        Returns:
            Jobs with the figures done taken from the ledger.
        """
        self.backfill()
        result = []
        for raw in self._load().values():
            try:
                spec = JobSpec.from_dict(raw["spec"])
            except (KeyError, TypeError, ValueError):
                continue
            entry = JobEntry(spec, dict(raw.get("settings") or {}), str(raw.get("last", "")),
                             int(raw.get("todo_bytes", 0)), int(raw.get("todo_files", 0)))
            if self.ledger is not None:
                sums = self.ledger.sums(spec)
                entry.read, entry.done_files = sums.read, sums.files
            result.append(entry)
        result.sort(key=lambda item: item.last, reverse=True)
        return result

    @staticmethod
    def line(entry: JobEntry) -> str:
        """Format one job for the selection list.

        Args:
            entry: The job.

        Returns:
            Text such as
            ``2026-11-13 21:40  ████░░░░  37.2 %  412 GiB done, 695 GiB to go  crf 28  /m/c``.
        """
        gib = 1024 ** 3
        todo = f"{entry.todo_bytes / gib:7.1f} GiB to go" if entry.planned else "to go: ?"
        return (f"{entry.last:<16}  {entry.gauge()} {entry.progress()}  "
                f"{entry.read / gib:7.1f} GiB done, {todo}  "
                f"crf {entry.settings.get('crf', 28)} {entry.settings.get('preset') or 'medium'}"
                f"  {entry.spec.label()}")
