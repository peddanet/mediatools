"""Stored measurements of videos, so a video is not decoded twice without a reason."""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Callable

from mediatools.broken.assessment import Assessment


class AssessmentStore:
    """Remembers the measurement of each video with its size and change time.

    A full measurement decodes the whole video (minutes for a long one).
    It is used again as long as the file is the same - same size, same
    modification time - and the measuring method has not changed. A new
    measurement is made when one of them differs, when the stored entry
    cannot be read (justified doubt), or when the caller asks for it
    (``--recheck``).

    The file ``assessments.json`` lies in the state folder (mode 600, it
    holds paths of the collection) and is written atomically after every
    new measurement, so an interrupted run keeps what it measured.

    Attributes:
        path: Location of the JSON file, or None to keep nothing.
        log: Logger.
        recheck: Measure again even if a stored measurement is valid.
        reused: Number of measurements taken from the store in this run.
        measured: Number of new measurements in this run.
    """

    FILE = "assessments.json"

    def __init__(self, path: str | None, logger, recheck: bool = False):
        """Bind the store to its file.

        Args:
            path: Location of ``assessments.json``; None stores nothing.
            logger: :class:`logging.Logger`.
            recheck: Ignore stored measurements.
        """
        self.path = path
        self.log = logger
        self.recheck = recheck
        self.reused = 0
        self.measured = 0
        self._data: dict[str, dict] = self._load()

    def _load(self) -> dict[str, dict]:
        """Read the file.

        Returns:
            Entries by absolute path; empty if missing or unreadable.
        """
        if not self.path or not os.path.isfile(self.path):
            return {}
        try:
            with open(self.path, encoding="utf-8", errors="surrogateescape") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            self.log.warning("stored measurements not readable - measuring again: %s", self.path)
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self) -> None:
        """Write the file atomically with mode 600.

        Entries another process saved meanwhile are kept (this process's
        own entries win), and the temporary file carries the process number,
        so two ``broken`` runs at the same time cannot spoil the file.
        """
        if not self.path:
            return
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        merged = self._load()
        merged.update(self._data)
        self._data = merged
        tmp = f"{self.path}.{os.getpid()}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", errors="surrogateescape") as handle:
            json.dump(self._data, handle, ensure_ascii=False, indent=0, sort_keys=True)
        os.replace(tmp, self.path)

    @staticmethod
    def identity(path: str) -> tuple[int, int] | None:
        """Return size and modification time (whole seconds) of a file.

        Args:
            path: File.

        Returns:
            ``(size, mtime)``, or None if the file cannot be read.
        """
        try:
            st = os.stat(path)
        except OSError:
            return None
        return st.st_size, int(st.st_mtime)

    def stored(self, path: str) -> tuple[Assessment, str] | None:
        """Return the valid stored measurement of a file.

        Args:
            path: Video file.

        Returns:
            ``(assessment, measured)``, or None if there is none, the file
            changed, the method is older or ``recheck`` is set.
        """
        if self.recheck:
            return None
        entry = self._data.get(os.path.abspath(path))
        ident = self.identity(path)
        if not isinstance(entry, dict) or ident is None:
            return None
        try:
            same = (int(entry["size"]), int(entry["mtime"])) == ident
            assessment = Assessment.from_dict(entry["assessment"])
        except (KeyError, TypeError, ValueError):
            return None
        if not same or not assessment.current:
            return None
        return assessment, str(entry.get("measured", ""))

    def put(self, path: str, assessment: Assessment) -> None:
        """Store a new measurement of a file.

        Args:
            path: Video file.
            assessment: Its measurement.
        """
        ident = self.identity(path)
        if ident is None:
            return
        self._data[os.path.abspath(path)] = {
            "size": ident[0], "mtime": ident[1], "assessment": assessment.to_dict(),
            "measured": datetime.now().isoformat(timespec="seconds")}
        self._save()

    def assess(self, path: str, measure: Callable[[str], Assessment]) -> Assessment:
        """Return the measurement of a file, measuring only if needed.

        Args:
            path: Video file.
            measure: The measurement (e.g. :meth:`Assessor.assess`).

        Returns:
            The stored or the new measurement.
        """
        found = self.stored(path)
        if found is not None:
            self.reused += 1
            self.log.info("  stored measurement of %s used (file unchanged)",
                          found[1] or "an earlier run")
            return found[0]
        assessment = measure(path)
        self.measured += 1
        self.put(path, assessment)
        return assessment
