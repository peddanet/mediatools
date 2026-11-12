"""Remembers the last form values of the GUI between sessions."""
from __future__ import annotations

import json
import os

from mediatools.core.datadir import DataDir


class FormStore:
    """Keeps the values of each command form in ``<data>/state/gui.json``.

    A form opens with the values of its last run, so a repeated command
    needs only a click. The file holds no secrets: only what was entered
    in the forms (paths and options). A damaged file is ignored.

    Attributes:
        path: Location of the JSON file.
    """

    def __init__(self, path: str | None = None):
        """Choose the file.

        Args:
            path: JSON file; ``<data>/state/gui.json`` if None.
        """
        self.path = path or os.path.join(DataDir().sub("state"), "gui.json")

    def _all(self) -> dict[str, dict[str, object]]:
        """Read the whole file.

        Returns:
            Values by command; empty if the file is missing or damaged.
        """
        try:
            with open(self.path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def load(self, command: str) -> dict[str, object]:
        """Return the remembered values of a command.

        Args:
            command: Subcommand name.

        Returns:
            Values by ``dest``; empty if none are remembered.
        """
        values = self._all().get(command, {})
        return values if isinstance(values, dict) else {}

    def save(self, command: str, values: dict[str, object]) -> None:
        """Remember the values of a command (atomic replace).

        Args:
            command: Subcommand name.
            values: Values by ``dest``.
        """
        data = self._all()
        data[command] = values
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=1, sort_keys=True)
        os.replace(tmp, self.path)
