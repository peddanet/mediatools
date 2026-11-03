"""Interactive choice of the backup disk."""
from __future__ import annotations

import json
import os

from mediatools.core.datadir import DataDir
from mediatools.core.prompt import Prompt
from mediatools.sync.fscheck import FsCheck


class TargetPicker:
    """Asks where the backup disk is mounted and remembers the answer.

    Candidates are mounted file systems below ``/media``, ``/run/media`` and
    ``/mnt``. The chosen target directory is stored per source in
    ``<data>/state/sync_targets.json`` and offered as default next time.

    Attributes:
        prompt: Prompt for the questions.
        fs: File-system helper.
        state: Path of the JSON file with remembered targets.
    """

    PREFIXES = ("/media/", "/run/media/", "/mnt")

    def __init__(self, prompt: Prompt | None = None, fs: FsCheck | None = None,
                 datadir: DataDir | None = None):
        """Wire the collaborators.

        Args:
            prompt: Prompt; a default one if None.
            fs: File-system helper; a default one if None.
            datadir: Data directory; the default one if None.
        """
        self.prompt = prompt or Prompt()
        self.fs = fs or FsCheck()
        self.state = os.path.join((datadir or DataDir()).sub("state"), "sync_targets.json")

    def candidates(self) -> list[tuple[str, str]]:
        """List mounted external disks.

        Returns:
            ``(mount point, fs type)`` pairs below the known prefixes.
        """
        seen, out = set(), []
        for _device, mount, fstype in self.fs.mounts():
            if mount.startswith(self.PREFIXES) and mount not in seen:
                seen.add(mount)
                out.append((mount, fstype))
        return out

    def _load(self) -> dict:
        """Read the remembered targets.

        Returns:
            Mapping source path to target path; empty if none stored.
        """
        try:
            with open(self.state, encoding="utf-8") as handle:
                return json.load(handle)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def remember(self, source: str, target: str) -> None:
        """Store the target for a source.

        Args:
            source: Source directory.
            target: Chosen target directory.
        """
        data = self._load()
        data[os.path.abspath(source)] = os.path.abspath(target)
        with open(self.state, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)

    def pick(self, source: str) -> str | None:
        """Ask for the target directory.

        Args:
            source: Source directory (used for the remembered default).

        Returns:
            Target directory, or None if the user aborted.
        """
        last = self._load().get(os.path.abspath(source))
        if last:
            print(f"Last target for this source: {last}")
            if self.prompt.yes_no("Use it again?", default=True):
                return last
        candidates = self.candidates()
        if not candidates:
            print("No mounted disk found below /media, /run/media or /mnt.")
            path = self.prompt.text("Enter the target directory manually (empty = abort)")
            return path or None
        print("Mounted disks:")
        for number, (mount, fstype) in enumerate(candidates, 1):
            try:
                free = self.fs.free_bytes(mount) / 1024 ** 4
                print(f"  [{number}] {mount}  ({fstype}, {free:.2f} TiB free)")
            except OSError:
                print(f"  [{number}] {mount}  ({fstype})")
        choice = self.prompt.text("Number of the backup disk (empty = abort)")
        if not choice.isdigit() or not 1 <= int(choice) <= len(candidates):
            return None
        mount = candidates[int(choice) - 1][0]
        default = os.path.join(mount, os.path.basename(os.path.abspath(source).rstrip("/")))
        return self.prompt.text("Target directory on that disk", default)
