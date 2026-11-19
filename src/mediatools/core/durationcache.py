"""Persistent cache of video durations for fast repeated triage scans."""
from __future__ import annotations

import os
from typing import Any


class DurationCache:
    """Remembers ffprobe durations keyed by path, size and mtime.

    Probing every video costs one ffprobe process. The cache makes the
    candidate count of a second run almost instant: an entry is used only
    while size and modification time of the file are unchanged.

    Stored as TSV ``path<TAB>size<TAB>mtime_ns<TAB>duration``.

    Several processes may use the same file at the same time (a shrink job
    and ``estimate``, for example). :meth:`save` therefore writes through a
    temporary file of its own process and first merges the entries that
    another process saved in the meantime, so neither a half-written file
    nor lost entries can result. An unreadable line is skipped.

    Attributes:
        path: Location of the TSV file.
        entries: Mapping path to ``(size, mtime_ns, duration)``.
        hits: Number of lookups answered from the cache in this run.
        added: Number of values stored since the last :meth:`save`.
    """

    def __init__(self, path: str):
        """Load the cache file if it exists.

        Args:
            path: Location of the TSV file (created by :meth:`save`).
        """
        self.path = path
        self.entries: dict[str, tuple[int, int, Any]] = self.read(path)
        self.hits = 0
        self.added = 0

    @classmethod
    def read(cls, path: str) -> dict[str, tuple[int, int, Any]]:
        """Read a cache file.

        Args:
            path: Location of the TSV file.

        Returns:
            Entries by path; empty if the file does not exist. Lines that
            cannot be read are skipped.
        """
        entries: dict[str, tuple[int, int, Any]] = {}
        try:
            with open(path, encoding="utf-8", errors="surrogateescape") as handle:
                for line in handle:
                    parts = line.rstrip("\n").split("\t")
                    if len(parts) != 4:
                        continue
                    try:
                        entries[parts[0]] = (int(parts[1]), int(parts[2]), cls.parse(parts[3]))
                    except ValueError:
                        continue
        except FileNotFoundError:
            pass
        return entries

    def get(self, file_path: str, st: os.stat_result) -> Any:
        """Return the cached duration if the file is unchanged.

        Args:
            file_path: Video file.
            st: Its current ``os.stat`` result.

        Returns:
            Duration in seconds, or None if unknown or outdated.
        """
        entry = self.entries.get(file_path)
        if entry and entry[0] == st.st_size and entry[1] == st.st_mtime_ns:
            self.hits += 1
            return entry[2]
        return None

    def put(self, file_path: str, st: os.stat_result, duration: Any) -> None:
        """Store a duration.

        Args:
            file_path: Video file.
            st: Its ``os.stat`` result at probing time.
            duration: Duration in seconds.
        """
        self.entries[file_path] = (st.st_size, st.st_mtime_ns, duration)
        self.added += 1

    def save(self) -> None:
        """Write the cache atomically, keeping what other processes saved meanwhile.

        The file is read again first; its entries are kept unless this
        process holds a value for the same path. The temporary file carries
        the process number, so two processes saving at the same moment never
        write into the same temporary file.
        """
        merged = self.read(self.path)
        merged.update(self.entries)
        self.entries = merged
        tmp = f"{self.path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8", errors="surrogateescape") as handle:
            for file_path, (size, mtime, duration) in sorted(self.entries.items()):
                handle.write(f"{file_path}\t{size}\t{mtime}\t{self.format(duration)}\n")
        os.replace(tmp, self.path)
        self.added = 0

    @staticmethod
    def parse(text: str) -> Any:
        """Convert a stored value.

        Args:
            text: Value column of the TSV file.

        Returns:
            Duration in seconds.
        """
        return float(text)

    @staticmethod
    def format(value: Any) -> str:
        """Convert a value for storage.

        Args:
            value: Duration in seconds.

        Returns:
            Text with millisecond precision.
        """
        return f"{value:.3f}"
