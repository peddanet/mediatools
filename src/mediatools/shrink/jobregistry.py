"""Remembered shrink jobs with their settings and progress."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from mediatools.shrink.jobspec import JobSpec
from mediatools.shrink.ledger import Ledger


@dataclass
class JobEntry:
    """A remembered job.

    Attributes:
        spec: Root and filters.
        settings: Settings of the last run: ``crf``, ``preset``, ``order``, ``jobs``.
        last: Time of the last activity, ``YYYY-MM-DD HH:MM``.
        todo_bytes: Source bytes still to encode (as of the last plan, minus
            the files finished since).
        todo_files: Files still to encode.
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
    def fraction(self) -> float:
        """Share of the job that is done (0..1)."""
        whole = self.read + self.todo_bytes
        return self.read / whole if whole else 0.0

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
        return args + self.spec.filters()

    def gauge(self, width: int = 20) -> str:
        """Return a text progress bar.

        Args:
            width: Number of cells.

        Returns:
            Text such as ``███████░░░░░░░░░░░░░``.
        """
        filled = int(round(self.fraction * width))
        return "█" * filled + "░" * (width - filled)


class JobRegistry:
    """``<state>/jobs.json``: the shrink jobs run so far.

    A job is entered when a run has planned it, with its settings and what
    is still to encode, and updated after every finished video, so the list
    shows the progress of a running job as well. The figures already done
    come from the :class:`Ledger` when the list is read.

    Attributes:
        path: Location of the JSON file.
        ledger: Source of the figures already done, or None.
    """

    def __init__(self, path: str, ledger: Ledger | None = None):
        """Bind the registry to its file.

        Args:
            path: Location of ``jobs.json``.
            ledger: Ledger for the figures already done; None leaves them 0.
        """
        self.path = path
        self.ledger = ledger

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

    def planned(self, spec: JobSpec, settings: dict[str, object], todo_bytes: int,
                todo_files: int) -> None:
        """Enter or update a job after its plan.

        Args:
            spec: Root and filters.
            settings: ``crf``, ``order``, ``jobs`` of this run.
            todo_bytes: Source bytes to encode.
            todo_files: Files to encode.
        """
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
        if entry is None:
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
        return (f"{entry.last:<16}  {entry.gauge()} {entry.fraction * 100:5.1f} %  "
                f"{entry.read / gib:7.1f} GiB done, {entry.todo_bytes / gib:7.1f} GiB to go  "
                f"crf {entry.settings.get('crf', 28)} {entry.settings.get('preset') or 'medium'}"
                f"  {entry.spec.label()}")
