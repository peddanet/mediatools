"""All ``_broken`` directories below a path."""
from __future__ import annotations

import os

from mediatools.broken.quarantine import Quarantine, QuarantineItem
from mediatools.core.mediatypes import MediaTypes
from mediatools.shrink.database import ShrinkDatabase


class QuarantineSet:
    """The ``_broken`` directory of a path plus every one found below it.

    Every path on which ``broken`` runs keeps its own ``_broken``
    directory, just as every directory keeps its own shrink database. A run
    on a parent folder therefore reads the ``_broken`` directories of the
    sub-folders used before, so that nothing is collected or measured
    twice and listing, backup check and review cover all of them. New finds
    always go into the ``_broken`` of the given path (:attr:`primary`).

    Attributes:
        primary: The quarantine of the given path (new finds go there).
        members: :attr:`primary` first, then every quarantine found below
            that has a manifest, in path order.
    """

    def __init__(self, root: str, logger, db_name: str = ShrinkDatabase.NAME):
        """Find the quarantines.

        Args:
            root: The given path.
            logger: :class:`logging.Logger`.
            db_name: File name of the shrink databases.
        """
        self.primary = Quarantine(root, logger, db_name)
        self.members: list[Quarantine] = [self.primary]
        for found in self.find(self.primary.root):
            self.members.append(Quarantine(found, logger, db_name))

    @staticmethod
    def find(root: str) -> list[str]:
        """Return the folders below a path that hold a ``_broken`` manifest.

        Args:
            root: Path to search (its own ``_broken`` is not included).

        Returns:
            Folders whose ``_broken/manifest.jsonl`` exists, sorted.
        """
        found = []
        for dirpath, dirs, _files in os.walk(root):
            if MediaTypes.BROKEN_DIR in dirs and dirpath != root and os.path.isfile(
                    os.path.join(dirpath, MediaTypes.BROKEN_DIR, Quarantine.MANIFEST)):
                found.append(dirpath)
            dirs[:] = sorted(name for name in dirs
                             if name not in MediaTypes.SKIP_DIRS and not name.startswith("."))
        return sorted(found)

    def open_items(self) -> list[tuple[Quarantine, QuarantineItem]]:
        """Return the videos still set aside in all quarantines.

        Returns:
            ``(quarantine, item)`` pairs, quarantine by quarantine.
        """
        return [(member, item) for member in self.members for item in member.open_items()]

    def holds(self, path: str) -> bool:
        """Tell whether any quarantine holds a video under this original name.

        Args:
            path: Original path.

        Returns:
            True if one of the quarantines has an open item with this name.
        """
        return any(member.holds(path) for member in self.members)

    def describe(self) -> list[str]:
        """Describe the quarantines for the start of a run.

        Returns:
            One line per quarantine with its location relative to the
            given path and its number of open videos.
        """
        lines = []
        for member in self.members:
            rel = os.path.relpath(member.directory, self.primary.root)
            lines.append(f"{rel}: {len(member.open_items())} video(s) set aside")
        return lines
