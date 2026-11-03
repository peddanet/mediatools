"""One-time renaming of shrink databases to the configured file name."""
from __future__ import annotations

import os

from mediatools.core.walker import TreeWalker


class DatabaseRenamer:
    """Renames ``<old>`` and ``<old>.bak_*`` to ``<new>`` and ``<new>.bak_*``.

    Used when the database file name changes, e.g. after setting
    ``[shrink] db_name``. Renaming within a directory keeps the inode, so
    the content is untouched. A directory in which the new name already
    exists is reported as a conflict and left alone; nothing is ever
    overwritten.

    Attributes:
        root: Absolute start directory.
        old: Current database file name.
        new: Target database file name.
        log: Logger.
        walker: Tree walker with the shared skip rules.
    """

    def __init__(self, root: str, old: str, new: str, logger):
        """Configure a run.

        Args:
            root: Start directory, processed recursively.
            old: Current database file name.
            new: Target database file name.
            logger: :class:`logging.Logger`.

        Raises:
            ValueError: If a name contains a path separator or both are equal.
        """
        if os.sep in old or os.sep in new:
            raise ValueError("database names must not contain a path separator")
        if old == new:
            raise ValueError("old and new database name are identical")
        self.root = os.path.abspath(root)
        self.old = old
        self.new = new
        self.log = logger
        self.walker = TreeWalker()

    def plan(self) -> list[tuple[str, str, str]]:
        """Find all files to rename.

        Returns:
            ``(source, destination, status)`` with status ``rename`` or
            ``conflict`` (destination exists), sorted by source path.
        """
        result = []
        for dirpath, _dirs, files in self.walker.walk(self.root):
            for name in files:
                if name == self.old:
                    new_name = self.new
                elif name.startswith(self.old + ".bak_"):
                    new_name = self.new + name[len(self.old):]
                else:
                    continue
                source = os.path.join(dirpath, name)
                destination = os.path.join(dirpath, new_name)
                status = "conflict" if os.path.lexists(destination) else "rename"
                result.append((source, destination, status))
        return sorted(result)

    def apply(self, plan: list[tuple[str, str, str]]) -> dict[str, int]:
        """Rename the planned files; conflicts are skipped.

        Args:
            plan: Result of :meth:`plan`.

        Returns:
            Counters ``renamed``, ``conflict`` and ``error``.
        """
        counts = {"renamed": 0, "conflict": 0, "error": 0}
        for source, destination, status in plan:
            if status != "rename" or os.path.lexists(destination):
                counts["conflict"] += 1
                self.log.warning("left alone (target exists): %s", source)
                continue
            try:
                os.rename(source, destination)
                counts["renamed"] += 1
                self.log.info("renamed: %s -> %s", source, os.path.basename(destination))
            except OSError as exc:
                counts["error"] += 1
                self.log.error("failed: %s (%s)", source, exc)
        return counts
