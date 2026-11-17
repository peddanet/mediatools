"""Finding the damaged videos of a shrink root."""
from __future__ import annotations

import csv
import os

from mediatools.broken.quarantine import Quarantine
from mediatools.core.mediatypes import MediaTypes
from mediatools.core.walker import TreeWalker
from mediatools.shrink.database import ShrinkDatabase


class BrokenFinder:
    """Lists the videos of a root that are known to be damaged.

    Two sources are read:

    * the shrink databases: every video recorded as ``BROKEN`` (its data
      failed the trial decode before encoding) with an unchanged size,
      except videos a user kept after a review;
    * optionally a report: a tab-separated file with a ``path`` column, e.g.
      the report of a full trial decode. If it has a ``check`` column, only
      rows whose check names an error or a broken file are taken.

    Videos recorded as ``FAILED`` (the encode failed, the source may be
    fine) are not listed.

    Attributes:
        root: Absolute shrink root.
        db_name: File name of the shrink databases.
        report: Optional report file.
    """

    MARKS = ("error", "broken")

    def __init__(self, root: str, db_name: str = ShrinkDatabase.NAME,
                 report: str | None = None):
        """Configure the finder.

        Args:
            root: Shrink root.
            db_name: File name of the shrink databases.
            report: Optional report file.
        """
        self.root = os.path.abspath(root)
        self.db_name = db_name
        self.report = report

    def find(self) -> list[tuple[str, str]]:
        """Collect the damaged videos.

        Returns:
            ``(path, reason)`` pairs, sorted by path, each path once.
        """
        found: dict[str, str] = {}
        for path, reason in self._from_databases():
            found.setdefault(path, reason)
        if self.report:
            for path, reason in self._from_report(self.report):
                found.setdefault(path, reason)
        return sorted(found.items())

    def _from_databases(self) -> list[tuple[str, str]]:
        """Read the ``BROKEN`` entries of all shrink databases.

        Returns:
            ``(path, reason)`` pairs of existing videos.
        """
        found = []
        for dirpath, _dirs, _files in TreeWalker().walk(self.root):
            database = ShrinkDatabase(dirpath, name=self.db_name)
            if not database.exists():
                continue
            for name, (_size, status) in database.failures().items():
                path = os.path.join(dirpath, name)
                if status != "BROKEN" or not os.path.isfile(path) \
                        or database.failure(path) != "BROKEN" or self._kept(database, name):
                    continue
                found.append((path, "recorded as BROKEN in " + database.name))
        return found

    @staticmethod
    def _kept(database: ShrinkDatabase, name: str) -> bool:
        """Tell whether a user kept a video after a review.

        Args:
            database: Shrink database of the video's directory.
            name: File name.

        Returns:
            True if the failure reason is the review mark.
        """
        return database.failure_reason(name) == Quarantine.KEPT_REASON

    def _from_report(self, report: str) -> list[tuple[str, str]]:
        """Read the videos of a report file.

        Args:
            report: Tab-separated file with a ``path`` column.

        Returns:
            ``(path, reason)`` pairs of existing videos below the root.

        Raises:
            SystemExit: If the file has no ``path`` column.
        """
        with open(report, encoding="utf-8", errors="surrogateescape", newline="") as handle:
            rows = list(csv.DictReader(handle, delimiter="\t"))
        if rows and "path" not in rows[0]:
            raise SystemExit(f"Error: the report has no 'path' column: {report}")
        found = []
        for row in rows:
            check = (row.get("check") or "").strip()
            if check and not any(mark in check.lower() for mark in self.MARKS):
                continue
            path = os.path.abspath(row.get("path") or "")
            inside = path.startswith(self.root + os.sep) and \
                os.sep + MediaTypes.BROKEN_DIR + os.sep not in path
            if inside and os.path.isfile(path):
                found.append((path, "report: " + (check or "listed")))
        return found
