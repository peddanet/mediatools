"""Marking a deleted video for a new download in a downloader's database."""
from __future__ import annotations

import os
import sqlite3


class DownloaderDatabase:
    """Lets a downloader fetch a deleted video again.

    Some downloaders keep a SQLite database per collection unit and skip
    every medium marked as downloaded there - also when the file was cut off
    by an interrupted download. After a damaged video is deleted, its row is
    set back to "not downloaded", so the next run of the downloader fetches
    it again (if the source still offers it).

    The database is searched at :attr:`relative` (e.g. a ``Metadata``
    folder) in the video's directory and every directory above it up to the
    shrink root. Expected table: ``medias`` with the columns ``directory``,
    ``filename`` and ``downloaded``. The row is chosen by file name; if the
    name occurs more than once, the row whose directory ends with the
    video's folder relative to the database's unit is taken. Nothing is
    changed when no row or no unique row matches.

    The feature is off until ``[broken] downloader_db`` names the relative
    path of the database file.

    Attributes:
        relative: Path of the database relative to a unit directory.
        log: Logger.
    """

    def __init__(self, relative: str, logger):
        """Store the settings.

        Args:
            relative: Path of the database file relative to a unit
                directory; empty switches the feature off.
            logger: :class:`logging.Logger`.
        """
        self.relative = relative.strip().strip("/")
        self.log = logger

    @property
    def enabled(self) -> bool:
        """Tell whether a database path is configured.

        Returns:
            True if :attr:`relative` is set.
        """
        return bool(self.relative)

    def locate(self, video: str, root: str) -> str | None:
        """Find the database responsible for a video.

        Args:
            video: Original path of the video.
            root: Shrink root (highest directory searched).

        Returns:
            Path of the database file, or None.
        """
        root = os.path.abspath(root)
        directory = os.path.dirname(os.path.abspath(video))
        while directory.startswith(root):
            candidate = os.path.join(directory, self.relative)
            if os.path.isfile(candidate):
                return candidate
            if directory == root:
                break
            directory = os.path.dirname(directory)
        return None

    def reset(self, video: str, root: str) -> str:
        """Mark a video as not downloaded.

        Args:
            video: Original path of the deleted video.
            root: Shrink root.

        Returns:
            Short result text for the log.
        """
        if not self.enabled:
            return "downloader database not configured ([broken] downloader_db)"
        database = self.locate(video, root)
        if database is None:
            return "no downloader database found"
        unit = database[:len(database) - len(self.relative) - 1]
        sub = os.path.relpath(os.path.dirname(os.path.abspath(video)), unit)
        try:
            return self._update(database, os.path.basename(video), sub)
        except sqlite3.Error as exc:
            return f"downloader database not changed: {exc}"

    def _update(self, database: str, name: str, sub: str) -> str:
        """Set ``downloaded = 0`` in the matching row.

        Args:
            database: Database file.
            name: File name of the video.
            sub: Folder of the video relative to the unit (``.`` for the unit).

        Returns:
            Short result text.
        """
        conn = sqlite3.connect(database, timeout=30)
        try:
            with conn:
                rows = conn.execute("SELECT rowid, directory FROM medias WHERE filename = ?",
                                    (name,)).fetchall()
                if len(rows) > 1:
                    tail = "" if sub == "." else "/" + sub.replace(os.sep, "/")
                    rows = [row for row in rows
                            if (row[1] or "").rstrip("/").endswith(tail)]
                if len(rows) != 1:
                    return (f"downloader database not changed: {len(rows)} matching rows "
                            f"for {name}")
                conn.execute("UPDATE medias SET downloaded = 0 WHERE rowid = ?", (rows[0][0],))
        finally:
            conn.close()
        self.log.debug("downloader database %s: %s marked as not downloaded", database, name)
        return "marked for a new download"
