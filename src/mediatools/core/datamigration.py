"""Move the data of releases before 1.5.0 into the XDG folders."""
from __future__ import annotations

import os
import shutil

from mediatools.core.datadir import DataDir


class DataMigration:
    """Moves ``~/scripts/mediatools.d`` content to config, state and data folders.

    Only data moves; the installed release (``venv``) and anything unknown
    stay where they are. A note ``WHERE-IS-THE-DATA.txt`` with the new
    places is left in the old folder. The move refuses to start while any
    other mediatools process runs (a running shrink keeps writing to the
    old folder) or when a target already exists (nothing is overwritten).

    Attributes:
        source: Old folder.
        target: Data folders of the XDG layout.
        proc: Root of the process table (``/proc``; replaceable in tests).
    """

    NOTE = DataDir.MOVED_NOTE
    STATE_ITEMS = ("logs", "state", "reports", "snapshot")
    DATA_ITEMS = ("plans", "releases")

    def __init__(self, source: str | None = None, target: DataDir | None = None,
                 proc: str = "/proc"):
        """Prepare a migration.

        Args:
            source: Old folder; ``~/scripts/mediatools.d`` if None.
            target: XDG folders; resolved without ``$MEDIATOOLS_HOME`` if None.
            proc: Root of the process table.
        """
        self.source = source or DataDir.legacy_dir()
        if target is None:
            target = DataDir.__new__(DataDir)
            target.mode = "xdg"
            target.config_dir = DataDir.xdg_dir("config")
            target.state_dir = target.root = DataDir.xdg_dir("state")
            target.data_dir = DataDir.xdg_dir("data")
        self.target = target
        self.proc = proc

    def moves(self) -> list[tuple[str, str]]:
        """List the planned moves of existing items.

        Returns:
            Pairs (old path, new path), only for items that exist.
        """
        pairs = [(name, os.path.join(self.target.state_dir, name)) for name in self.STATE_ITEMS]
        pairs += [(name, os.path.join(self.target.data_dir, name)) for name in self.DATA_ITEMS]
        pairs += [(DataDir.CONFIG_FILE, self.target.config_file),
                  ("privacy_denylist.txt", self.target.privacy_file("deny")),
                  ("privacy_allowlist.txt", self.target.privacy_file("allow"))]
        return [(os.path.join(self.source, name), new) for name, new in pairs
                if os.path.lexists(os.path.join(self.source, name))]

    def others(self) -> list[tuple[int, str]]:
        """Find other running mediatools processes.

        Returns:
            (pid, command line) of every process except this one whose
            program or module (first three arguments) is mediatools.
        """
        found: list[tuple[int, str]] = []
        own = os.getpid()
        try:
            entries = os.listdir(self.proc)
        except OSError:
            return found
        for entry in entries:
            if not entry.isdigit() or int(entry) == own:
                continue
            try:
                with open(os.path.join(self.proc, entry, "cmdline"), "rb") as handle:
                    args = handle.read().split(b"\0")
            except OSError:
                continue
            words = [arg.decode("utf-8", "replace") for arg in args if arg]
            if any(os.path.basename(word) == "mediatools" for word in words[:3]):
                found.append((int(entry), " ".join(words)))
        return sorted(found)

    def problems(self) -> list[str]:
        """Collect the reasons that forbid the move.

        Returns:
            One message per problem; empty if the move may run.
        """
        found = [f"another mediatools process runs (pid {pid}: {line}) - stop or close it first"
                 for pid, line in self.others()]
        found += [f"target exists, nothing is overwritten: {new}"
                  for _old, new in self.moves() if os.path.lexists(new)]
        return found

    def run(self) -> list[tuple[str, str]]:
        """Move the items and leave the note.

        Returns:
            The moves done.

        Raises:
            RuntimeError: If :meth:`problems` reports anything.
        """
        problems = self.problems()
        if problems:
            raise RuntimeError("; ".join(problems))
        done = self.moves()
        for old, new in done:
            os.makedirs(os.path.dirname(new), exist_ok=True)
            shutil.move(old, new)
            if os.path.basename(os.path.dirname(new)) == "privacy" or new.endswith(".ini"):
                os.chmod(new, 0o600)
        with open(os.path.join(self.source, self.NOTE), "w", encoding="utf-8") as handle:
            handle.write("Since mediatools 1.5.0 the data lives in the XDG folders:\n"
                         f"  configuration  {self.target.config_dir}\n"
                         f"  logs, state    {self.target.state_dir}\n"
                         f"  kept data      {self.target.data_dir}\n"
                         "Here stays only the installed release (venv).\n"
                         "Show all places with: mediatools where\n")
        return done
