"""Location of runtime data (logs, state files, plans, reports)."""
from __future__ import annotations

import os
from datetime import datetime


class DataDir:
    """Runtime data directory of mediatools.

    The root is taken from the environment variable ``MEDIATOOLS_HOME``;
    the default is ``~/scripts/mediatools.d``. Nothing is written into the
    media collection itself except what a subcommand documents (shrink
    databases, ``_flat`` directories).

    Layout::

        <root>/logs/      one log file per run, date and time in the name
        <root>/state/     persistent state, e.g. triage kept list
        <root>/plans/     flatten plans (for review and undo)
        <root>/reports/   progress lists and sync reports

    Attributes:
        root: Absolute path of the data root.
    """

    ENV = "MEDIATOOLS_HOME"
    DEFAULT = "~/scripts/mediatools.d"

    def __init__(self, root: str | None = None):
        """Resolve the data root.

        Args:
            root: Explicit root; if None, ``$MEDIATOOLS_HOME`` or the default
                is used.
        """
        base = root or os.environ.get(self.ENV) or self.DEFAULT
        self.root = os.path.abspath(os.path.expanduser(base))

    def sub(self, name: str) -> str:
        """Return a subdirectory of the data root, creating it if needed.

        Args:
            name: Subdirectory name, e.g. ``logs``.

        Returns:
            Absolute path of the subdirectory.
        """
        path = os.path.join(self.root, name)
        os.makedirs(path, exist_ok=True)
        return path

    def file(self, sub: str, prefix: str, suffix: str) -> str:
        """Build a time-stamped file path inside a subdirectory.

        Args:
            sub: Subdirectory name, e.g. ``logs``.
            prefix: File name prefix, e.g. ``shrink``.
            suffix: File name suffix including the dot, e.g. ``.log``.

        Returns:
            Path such as ``<root>/logs/shrink_2026-11-03_13-12-52.log``.
        """
        return os.path.join(self.sub(sub), f"{prefix}_{self.stamp()}{suffix}")

    @staticmethod
    def stamp() -> str:
        """Return the current time in the format used in file names.

        Returns:
            Timestamp ``YYYY-MM-DD_hh-mm-ss``.
        """
        return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
