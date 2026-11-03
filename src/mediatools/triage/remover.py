"""Moving files to the trash or deleting them."""
from __future__ import annotations

import os
import subprocess


class FileRemover:
    """Removes files either via ``gio trash`` (default) or permanently.

    ``gio trash`` follows the FreeDesktop trash specification; trashed files
    can be listed with ``gio trash --list`` and restored with
    ``gio trash --restore <uri>``. Trashed files still occupy disk space
    until the trash is emptied.

    Attributes:
        permanent: Delete instead of trashing.
        log: Optional logger.
        gio: Name or path of the gio executable.
    """

    def __init__(self, permanent: bool = False, logger=None, gio: str = "gio"):
        """Configure the remover.

        Args:
            permanent: Delete instead of trashing.
            logger: Optional :class:`logging.Logger`.
            gio: Name or path of the gio executable.
        """
        self.permanent = permanent
        self.log = logger
        self.gio = gio

    @property
    def verb(self) -> str:
        """Human readable action.

        Returns:
            ``delete permanently`` or ``move to trash``.
        """
        return "delete permanently" if self.permanent else "move to trash"

    def remove(self, path: str) -> bool:
        """Remove one file.

        Args:
            path: File to remove.

        Returns:
            True on success, False if the removal failed (logged).
        """
        try:
            if self.permanent:
                os.remove(path)
            else:
                subprocess.run([self.gio, "trash", "--", path], check=True,
                               capture_output=True, text=True)
            if self.log:
                self.log.info("REMOVED (%s): %s",
                              "deleted" if self.permanent else "trashed", path)
            return True
        except (OSError, subprocess.CalledProcessError) as exc:
            if self.log:
                self.log.error("could not remove %s: %s", path, exc)
            return False
