"""Bookkeeping of the parallel encode slots of a shrink run."""
from __future__ import annotations

import os
import time
from collections import Counter
from concurrent.futures import Future
from dataclasses import dataclass, field

from mediatools.shrink.database import ShrinkDatabase


@dataclass
class RunningEncode:
    """One encode in a slot.

    Attributes:
        slot: Slot number (index of its encoder).
        path: Original video.
        size: Its size.
        out: Output path in ``newShrinkedFiles/``.
        database: Database of its directory.
        inode: ``(device, inode)`` of the original, or None.
        length: Playing time, or None.
        started: Monotonic start time.
        cell: ``[name, encoded fraction, size]`` for the display; the
            worker thread updates the fraction.
    """

    slot: int
    path: str
    size: int
    out: str
    database: ShrinkDatabase
    inode: tuple[int, int] | None
    length: float | None
    started: float = field(default_factory=time.monotonic)
    cell: list = field(default_factory=list)


class SlotTable:
    """Which slots are free, what runs where, and what must not run together.

    Two names of one inode (hard links) must not be encoded at the same
    time: both replacements would write the same file. A work folder
    ``newShrinkedFiles/`` may be removed only when no encode of its
    directory runs. Only the main thread uses this table; the worker
    threads only write the fraction into their :attr:`RunningEncode.cell`.

    Attributes:
        free: Free slot numbers.
        running: Encodes by their future.
        inodes: Inodes being encoded.
        folders: Running encodes per directory.
    """

    def __init__(self, slots: int):
        """Start with all slots free.

        Args:
            slots: Number of slots.
        """
        self.free = list(range(slots))[::-1]
        self.running: dict[Future, RunningEncode] = {}
        self.inodes: set[tuple[int, int]] = set()
        self.folders: Counter = Counter()

    @staticmethod
    def inode(path: str) -> tuple[int, int] | None:
        """Return device and inode of a file.

        Args:
            path: File.

        Returns:
            ``(st_dev, st_ino)``, or None if the file is gone.
        """
        try:
            info = os.stat(path)
        except OSError:
            return None
        return info.st_dev, info.st_ino

    def blocked(self, path: str) -> bool:
        """Return whether another name of this file is being encoded.

        Args:
            path: Planned video.

        Returns:
            True if it has to wait.
        """
        inode = self.inode(path)
        return inode is not None and inode in self.inodes

    def take(self, future: Future, item: RunningEncode) -> None:
        """Record an encode that was started in ``item.slot``.

        Args:
            future: Its future.
            item: The encode.
        """
        self.running[future] = item
        if item.inode is not None:
            self.inodes.add(item.inode)
        self.folders[os.path.dirname(item.path)] += 1

    def release(self, future: Future) -> tuple[RunningEncode, bool]:
        """Free the slot of a finished encode.

        Args:
            future: Its future.

        Returns:
            The encode and whether no other encode of its directory runs
            (its work folder may then be removed).
        """
        item = self.running.pop(future)
        self.free.append(item.slot)
        if item.inode is not None:
            self.inodes.discard(item.inode)
        folder = os.path.dirname(item.path)
        self.folders[folder] -= 1
        return item, self.folders[folder] <= 0

    def cells(self) -> list[tuple[str, float, int]]:
        """Return the display data of the running encodes.

        Returns:
            ``(name, fraction, size)`` per running encode, in slot order.
        """
        items = sorted(self.running.values(), key=lambda item: item.slot)
        return [(item.cell[0], item.cell[1], item.cell[2]) for item in items]
