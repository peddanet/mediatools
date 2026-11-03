"""Validated snapshot settings taken from the configuration file."""
from __future__ import annotations

import os
from dataclasses import dataclass

from mediatools.core.config import Config


@dataclass(frozen=True)
class SnapshotSettings:
    """All values needed by :class:`Snapshotter`, already checked.

    Attributes:
        source: Absolute directory that is backed up.
        disk: Mount point of the backup disk.
        root: Absolute snapshot root on the disk.
        retain: ``(level, count)`` from the most to the least frequent level.
        exclude: rsync exclude patterns.
        max_deletions: Abort threshold for files removed from the mirror.
    """

    source: str
    disk: str
    root: str
    retain: tuple[tuple[str, int], ...]
    exclude: tuple[str, ...]
    max_deletions: int

    LEVELS = ("alpha", "beta", "gamma", "delta")
    PERIOD_DAYS = {"beta": 7, "gamma": 30, "delta": 365}

    @classmethod
    def from_config(cls, config: Config) -> "SnapshotSettings":
        """Read and validate the ``[snapshot]`` section.

        Args:
            config: Loaded configuration.

        Returns:
            The settings.

        Raises:
            SystemExit: With an explanation of the expected value if
                something is missing or inconsistent.
        """
        source = config.path_value("snapshot", "source")
        disk = config.path_value("snapshot", "disk")
        if not source or not disk:
            missing = [key for key, value in (("source", source), ("disk", disk)) if not value]
            raise SystemExit(
                "Error: [snapshot] " + ", ".join(missing) + f" not set in {config.path}\n"
                "  source = directory to back up, e.g. ~/Pictures/collection\n"
                "  disk   = mount point of the backup disk, e.g. /media/USER/BACKUP\n"
                "Show mount points with: lsblk -o NAME,SIZE,FSTYPE,LABEL,MOUNTPOINTS")
        relative = config.get("snapshot", "root") or "rsnapshot"
        if os.path.isabs(relative) or ".." in relative.split("/"):
            raise SystemExit("Error: [snapshot] root must be a directory name relative "
                             f"to the disk, found {relative!r}")
        retain = []
        for level in cls.LEVELS:
            count = config.integer("snapshot", f"retain_{level}")
            if count < 0:
                raise SystemExit(f"Error: [snapshot] retain_{level} must be >= 0")
            if count:
                retain.append((level, count))
        if not retain or retain[0][0] != "alpha":
            raise SystemExit("Error: [snapshot] retain_alpha must be at least 1")
        exclude = tuple(p.strip() for p in config.get("snapshot", "exclude").split(",")
                        if p.strip())
        return cls(source=source, disk=disk, root=os.path.join(disk, relative),
                   retain=tuple(retain), exclude=exclude,
                   max_deletions=config.integer("snapshot", "max_deletions"))

    def mirror(self, base: str) -> str:
        """Return where the source lives inside a snapshot directory.

        rsnapshot is used with ``--relative`` and the backup destination
        ``localhost/``, so ``/home/u/coll`` ends up in
        ``<base>/localhost/home/u/coll``.

        Args:
            base: Snapshot directory, e.g. ``<root>/.sync`` or ``<root>/alpha.0``.

        Returns:
            Absolute path of the mirrored source inside ``base``.
        """
        return os.path.join(base, "localhost", self.source.lstrip(os.sep))
