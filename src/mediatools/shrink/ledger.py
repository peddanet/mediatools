"""Append-only record of every video a shrink run finished."""
from __future__ import annotations

import os
from dataclasses import dataclass

from mediatools.shrink.jobspec import JobSpec
from mediatools.shrink.ledgerrow import LedgerRow
from mediatools.shrink.logbackfill import LogBackfill


@dataclass
class LedgerSums:
    """Totals over a selection of ledger entries.

    Attributes:
        read: Source bytes of the encoded videos (``shrunk``, ``copy``,
            ``FAILED``).
        files: Number of those videos.
        freed_src: Source bytes of the shrunk videos.
        freed_out: Output bytes of the shrunk videos.
        since: Date of the first entry (``YYYY-MM-DD``), empty if none.
    """

    read: int = 0
    files: int = 0
    freed_src: int = 0
    freed_out: int = 0
    since: str = ""

    def add(self, row: LedgerRow) -> None:
        """Count one entry.

        Args:
            row: Ledger entry.
        """
        if row.status in JobSpec.READ:
            self.read += row.source
            self.files += 1
            self.since = self.since or row.when[:10]
        if row.status == "shrunk" and row.result is not None:
            self.freed_src += row.source
            self.freed_out += row.result


class Ledger:
    """``<data>/ledger.tsv``: one line per finished video, never rewritten.

    The ledger is the source of the job, session and overall figures. It
    lives with the kept data (``~/.local/share/mediatools``), is only
    appended to and has mode 600, as it lists paths of the collection.
    If it does not exist, it is filled in once from the result tables of
    the shrink logs (:class:`LogBackfill`), so nothing counted before 1.6.0
    is lost. Resetting the overall figures therefore takes deleting the
    ledger *and* the logs on purpose.

    Attributes:
        path: Location of the ledger.
        logs: Folder of the shrink logs (source of the first filling).
        cache: Parsed entries with the file size they were read at.
    """

    HEADER = ("# mediatools ledger v1: when, session, status, source bytes, result bytes, "
              "seconds, playing seconds, mtime, path\n")

    def __init__(self, path: str, logs: str | None = None):
        """Bind the ledger to its file.

        Args:
            path: Location of ``ledger.tsv``.
            logs: Folder of the shrink logs; None never fills in.
        """
        self.path = path
        self.logs = logs
        self.cache: tuple[int, list[LedgerRow]] = (-1, [])

    def ensure(self) -> int:
        """Create the ledger, filled in from the logs, if it does not exist.

        Returns:
            Number of entries taken from the logs (0 if it existed).
        """
        if os.path.exists(self.path):
            return 0
        rows = LogBackfill(self.logs).rows() if self.logs else []
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        descriptor = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(self.HEADER)
            for row in rows:
                handle.write(row.line() + "\n")
        os.replace(tmp, self.path)
        return len(rows)

    def add(self, row: LedgerRow) -> None:
        """Append one entry and flush it.

        Args:
            row: The finished video.
        """
        self.ensure()
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(row.line() + "\n")
            handle.flush()

    def rows(self) -> list[LedgerRow]:
        """Return all entries (read again only when the file grew).

        Returns:
            Entries, oldest first; empty if there is no ledger.
        """
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return []
        if size != self.cache[0]:
            with open(self.path, encoding="utf-8", errors="replace") as handle:
                parsed = [LedgerRow.parse(line) for line in handle]
            self.cache = (size, [row for row in parsed if row is not None])
        return self.cache[1]

    def sums(self, job: JobSpec | None = None, session: str | None = None) -> LedgerSums:
        """Sum the entries of a job, of a session, or all.

        Args:
            job: Only entries of this job; None for all.
            session: Only entries of this session; None for all.

        Returns:
            Totals.
        """
        result = LedgerSums()
        for row in self.rows():
            if session is not None and row.session != session:
                continue
            if job is not None and not job.matches(row):
                continue
            result.add(row)
        return result
