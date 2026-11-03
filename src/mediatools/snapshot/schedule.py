"""Decides which snapshot levels are due when the disk is attached."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

from mediatools.core.datadir import DataDir
from mediatools.snapshot.settings import SnapshotSettings


class LevelSchedule:
    """Remembers when each higher level was last rotated.

    The backup disk is not permanently attached, so fixed timer dates do
    not fit. Instead every run rotates ``alpha`` and additionally each
    higher level whose period (beta 7, gamma 30, delta 365 days) has passed
    since its last successful rotation. rsnapshot requires the higher
    levels to be rotated first, so :meth:`due` returns them from the least
    to the most frequent.

    Attributes:
        settings: Validated settings.
        path: JSON state file ``{level: ISO time}``.
    """

    def __init__(self, settings: SnapshotSettings, datadir: DataDir | None = None):
        """Locate the state file.

        Args:
            settings: Validated settings.
            datadir: Data directory; the default one if None.
        """
        self.settings = settings
        self.path = os.path.join((datadir or DataDir()).sub("state"), "snapshot_levels.json")

    def load(self) -> dict[str, str]:
        """Read the state file.

        Returns:
            Mapping level to ISO time of the last rotation; empty if none.
        """
        if not os.path.exists(self.path):
            return {}
        with open(self.path, encoding="utf-8") as handle:
            return dict(json.load(handle))

    def due(self, now: datetime) -> list[str]:
        """List the higher levels to rotate in this run.

        Args:
            now: Current time.

        Returns:
            Level names, least frequent first; ``alpha`` is not included.
        """
        state = self.load()
        result = []
        for level, _count in reversed(self.settings.retain[1:]):
            last = state.get(level)
            period = timedelta(days=SnapshotSettings.PERIOD_DAYS[level])
            if last is None or now - datetime.fromisoformat(last) >= period:
                result.append(level)
        return result

    def record(self, level: str, when: datetime) -> None:
        """Store the time of a successful rotation.

        Args:
            level: Level name.
            when: Time of the rotation.
        """
        state = self.load()
        state[level] = when.isoformat(timespec="seconds")
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
