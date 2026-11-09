"""Crash-safe replacement of an original video by its validated output."""
from __future__ import annotations

import os
import shutil
from collections.abc import Callable

from mediatools.core.mediatypes import MediaTypes
from mediatools.shrink.database import ShrinkDatabase


class ReplaceJournal:
    """Copies a validated output over its original with a journal marker.

    **Crash safety.** The validated output is flushed to disk, then a
    marker ``newShrinkedFiles/<name>.replace`` is written and flushed, and
    the directory entry is flushed; only then is the original overwritten
    (in place: the inode is kept, so ``_flat`` hard links see the new
    content). The marker is removed after the copy has been flushed and
    recorded. If the machine fails in between, :meth:`recover` finds the
    marker next to the complete output in the next run and repeats the
    copy before anything else.

    Attributes:
        log: Logger.
    """

    MARKER = ".replace"

    def __init__(self, logger) -> None:
        """Store the logger.

        Args:
            logger: :class:`logging.Logger` for recovery messages.
        """
        self.log = logger

    def replace(self, out: str, path: str, database: ShrinkDatabase) -> None:
        """Copy a validated output over the original and record it.

        Order: flush the output, write and flush the marker, flush the
        directory entry, copy into the existing inode, flush, record, delete
        output, delete marker. A crash at any point leaves either the
        untouched original or the marker plus the complete validated output;
        repeating the copy is harmless, so a crash after step 5 only causes
        one more identical copy on the next run.

        Args:
            out: Validated output in ``newShrinkedFiles/``.
            path: Original video.
            database: Database of its directory.
        """
        marker = out + self.MARKER
        self.fsync_file(out)                         # 1. output complete on disk
        with open(marker, "w", encoding="utf-8") as handle:
            handle.write(path + "\n")
            handle.flush()
            os.fsync(handle.fileno())                # 2. marker content on disk
        self.fsync_dir(os.path.dirname(marker))      # 3. marker's directory entry on disk
        shutil.copyfile(out, path)                   # 4. writes into the existing inode
        self.fsync_file(path)                        # 5. new content of the original on disk
        database.ensure()
        database.record(path)
        database.clear_failure(path)
        os.remove(out)
        os.remove(marker)

    def recover(self, dirpath: str, database_of: Callable[[str], ShrinkDatabase]) -> int:
        """Repeat replacements that a crash interrupted.

        Args:
            dirpath: Directory whose ``newShrinkedFiles`` is checked.
            database_of: Returns the database of a directory.

        Returns:
            Number of replacements completed.
        """
        tmp = os.path.join(dirpath, MediaTypes.SHRINK_TMP_DIR)
        if not os.path.isdir(tmp):
            return 0
        done = 0
        for entry in sorted(os.listdir(tmp)):
            if not entry.endswith(self.MARKER):
                continue
            out = os.path.join(tmp, entry[:-len(self.MARKER)])
            target = os.path.join(dirpath, os.path.basename(out))
            if os.path.isfile(out) and os.path.isfile(target):
                self.log.warning("completing an interrupted replacement: %s", target)
                self.replace(out, target, database_of(dirpath))
                done += 1
            else:
                try:
                    os.remove(os.path.join(tmp, entry))
                except FileNotFoundError:
                    pass
        return done

    @staticmethod
    def fsync_file(path: str) -> None:
        """Force a file's content to disk.

        Args:
            path: File.
        """
        with open(path, "rb+") as handle:
            os.fsync(handle.fileno())

    @staticmethod
    def fsync_dir(path: str) -> None:
        """Force a directory's entries (file creations) to disk.

        Args:
            path: Directory.
        """
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
