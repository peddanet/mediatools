"""Per-directory SQLite database of already shrunk files."""
from __future__ import annotations

import contextlib
import os
import shutil
import sqlite3
from collections.abc import Iterator
from datetime import datetime


class ShrinkDatabase:
    """The shrink database file of one directory.

    One database exists per directory that contains videos. A row means
    "this file has been processed": either shrunk by ``mediatools shrink`` or
    found to be HEVC already. A file is skipped when path, size and mtime
    all match a row.

    The file name is configurable (``[shrink] db_name``, default
    ``shrink.db``); ``mediatools rename-db`` renames databases created under
    another name. Databases with an early schema (no PRIMARY KEY) are backed
    up and migrated on first use.

    Schema::

        CREATE TABLE files (path text NOT NULL PRIMARY KEY,
                            size integer, modified text)

    Attributes:
        name: File name of the database.
        path: Absolute path of the database file.
        log: Optional logger.
        migrated_backup: Path of the backup made during a migration, if any.
    """

    NAME = "shrink.db"
    SCHEMA = ("CREATE TABLE IF NOT EXISTS files "
              "(path text NOT NULL PRIMARY KEY, size integer, modified text)")

    def __init__(self, directory: str, logger=None, name: str | None = None):
        """Bind the object to a directory.

        Args:
            directory: Directory whose database is handled.
            logger: Optional :class:`logging.Logger`.
            name: Database file name; :attr:`NAME` if None.
        """
        self.name = name or self.NAME
        self.path = os.path.join(directory, self.name)
        self.log = logger
        self.migrated_backup: str | None = None

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def mtime_text(path: str) -> str:
        """Format the mtime of a file as stored in the database.

        Args:
            path: File whose mtime is read.

        Returns:
            Local time ``YYYY-MM-DD HH:MM:SS``.
        """
        return datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M:%S")

    @contextlib.contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Open the database for one transaction and always close it.

        ``with sqlite3.connect(...)`` alone only commits or rolls back; it
        does not close the connection. This wrapper does both.

        Yields:
            Open connection; the 30 s busy timeout covers a GUI holding a lock.
        """
        conn = sqlite3.connect(self.path, timeout=30)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def exists(self) -> bool:
        """Tell whether the database file exists.

        Returns:
            True if the file exists.
        """
        return os.path.exists(self.path)

    # -- lifecycle -------------------------------------------------------
    def ensure(self) -> None:
        """Create the database or migrate an old schema."""
        if not self.exists():
            with self._connect() as conn:
                conn.execute(self.SCHEMA)
            return
        with self._connect() as conn:
            row = conn.execute("SELECT sql FROM sqlite_master "
                               "WHERE type='table' AND name='files'").fetchone()
        if row is None:
            # file exists but has no table yet
            with self._connect() as conn:
                conn.execute(self.SCHEMA)
            return
        if "PRIMARY KEY" not in row[0].upper():
            self._migrate()

    def _migrate(self) -> None:
        """Back up the file and rebuild the table with a PRIMARY KEY.

        Duplicate paths keep the most recently inserted row; rows without a
        path are dropped.
        """
        backup = self.path + ".bak_" + datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        shutil.copy2(self.path, backup)
        with self._connect() as conn:
            conn.executescript("""
                BEGIN;
                CREATE TABLE files_new (path text NOT NULL PRIMARY KEY,
                                        size integer, modified text);
                INSERT OR REPLACE INTO files_new
                  SELECT path, size, modified FROM files
                  WHERE path IS NOT NULL ORDER BY rowid;
                DROP TABLE files;
                ALTER TABLE files_new RENAME TO files;
                COMMIT;""")
        self.migrated_backup = backup
        if self.log:
            self.log.warning("migrated old schema of %s (backup %s)", self.path, backup)

    # -- queries ---------------------------------------------------------
    def is_done(self, file_path: str) -> bool:
        """Tell whether a file is recorded with its current size and mtime.

        Args:
            file_path: Absolute path of the video.

        Returns:
            True if a row with identical path, size and mtime exists.
        """
        if not self.exists():
            return False
        size = os.path.getsize(file_path)
        mtime = self.mtime_text(file_path)
        with self._connect() as conn:
            row = conn.execute("SELECT 1 FROM files WHERE path=? AND size=? AND modified=?",
                               (file_path, size, mtime)).fetchone()
        return row is not None

    def record(self, file_path: str) -> None:
        """Insert or update the row of a file with its current size and mtime.

        Args:
            file_path: Absolute path of the video.
        """
        size = os.path.getsize(file_path)
        mtime = self.mtime_text(file_path)
        with self._connect() as conn:
            conn.execute("INSERT INTO files (path, size, modified) VALUES (?, ?, ?) "
                         "ON CONFLICT(path) DO UPDATE SET size=excluded.size, "
                         "modified=excluded.modified", (file_path, size, mtime))
        if self.log:
            self.log.info("DB record: %s (size=%i, modified=%s)", file_path, size, mtime)

    def rows(self) -> list[tuple]:
        """Return all rows (for reports and tests).

        Returns:
            List of ``(path, size, modified)``; empty if no database exists.
        """
        if not self.exists():
            return []
        with self._connect() as conn:
            return conn.execute("SELECT path, size, modified FROM files").fetchall()
