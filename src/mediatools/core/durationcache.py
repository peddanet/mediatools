"""Persistent cache of video durations for fast repeated triage scans."""
from __future__ import annotations

import os


class DurationCache:
    """Remembers ffprobe durations keyed by path, size and mtime.

    Probing every video costs one ffprobe process. The cache makes the
    candidate count of a second run almost instant: an entry is used only
    while size and modification time of the file are unchanged.

    Stored as TSV ``path<TAB>size<TAB>mtime_ns<TAB>duration``.

    Attributes:
        path: Location of the TSV file.
        entries: Mapping path to ``(size, mtime_ns, duration)``.
        hits: Number of lookups answered from the cache in this run.
    """

    def __init__(self, path: str):
        """Load the cache file if it exists.

        Args:
            path: Location of the TSV file (created by :meth:`save`).
        """
        self.path = path
        self.entries: dict[str, tuple[int, int, float]] = {}
        self.hits = 0
        try:
            with open(path, encoding="utf-8", errors="surrogateescape") as handle:
                for line in handle:
                    parts = line.rstrip("\n").split("\t")
                    if len(parts) == 4:
                        self.entries[parts[0]] = (int(parts[1]), int(parts[2]), float(parts[3]))
        except FileNotFoundError:
            pass

    def get(self, file_path: str, st: os.stat_result) -> float | None:
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

    def put(self, file_path: str, st: os.stat_result, duration: float) -> None:
        """Store a duration.

        Args:
            file_path: Video file.
            st: Its ``os.stat`` result at probing time.
            duration: Duration in seconds.
        """
        self.entries[file_path] = (st.st_size, st.st_mtime_ns, duration)

    def save(self) -> None:
        """Write the cache atomically (temporary file, then rename)."""
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8", errors="surrogateescape") as handle:
            for file_path, (size, mtime, duration) in sorted(self.entries.items()):
                handle.write(f"{file_path}\t{size}\t{mtime}\t{duration:.3f}\n")
        os.replace(tmp, self.path)
