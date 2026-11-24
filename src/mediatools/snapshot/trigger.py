"""systemd user unit that starts a snapshot run when the disk is mounted."""
from __future__ import annotations

import os
import shutil
import string
import subprocess


class SystemdTrigger:
    """Creates ``~/.config/systemd/user/mediatools-snapshot.service``.

    The service is ``WantedBy`` the mount unit of the backup disk, so the
    user's systemd instance starts it whenever the desktop mounts the disk.
    Mount units of all mounted file systems are visible to the user
    instance; :meth:`mount_unit_known` verifies that for the actual disk
    before anything is installed. The run uses ``-y``: it never asks, and it
    aborts instead if the deletion limit would be exceeded.

    Attributes:
        disk: Mount point of the backup disk.
        launcher: Command that starts mediatools.
        unit_dir: Directory of user units.
    """

    NAME = "mediatools-snapshot.service"

    def __init__(self, disk: str, launcher: str, unit_dir: str | None = None):
        """Configure the trigger.

        Args:
            disk: Mount point of the backup disk.
            launcher: Absolute path of the ``mediatools`` launcher.
            unit_dir: Directory of user units; ``~/.config/systemd/user`` if None.
        """
        self.disk = disk
        self.launcher = launcher
        self.unit_dir = unit_dir or os.path.expanduser("~/.config/systemd/user")

    def mount_unit(self) -> str:
        """Return the systemd mount unit name of the disk.

        Returns:
            Name such as ``media-user-BACKUP.mount``.

        Raises:
            SystemExit: If ``systemd-escape`` is not available.
        """
        escape = shutil.which("systemd-escape")
        if escape is None:
            raise SystemExit("Error: systemd-escape not found (no systemd?)")
        res = subprocess.run([escape, "--path", "--suffix=mount", self.disk],
                             capture_output=True, text=True, check=True)
        return res.stdout.strip()

    def mount_unit_known(self) -> bool:
        """Ask the user's systemd instance whether the mount unit is active.

        Returns:
            True if ``systemctl --user`` reports the unit as active.
        """
        systemctl = shutil.which("systemctl")
        if systemctl is None:
            return False
        res = subprocess.run([systemctl, "--user", "is-active", self.mount_unit()],
                             capture_output=True, text=True, check=False)
        return res.stdout.strip() == "active"

    def render(self) -> str:
        """Build the unit file from ``mediatools/data/mediatools-snapshot.service.in``.

        Returns:
            Content of the service file.
        """
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "data", "mediatools-snapshot.service.in")
        with open(path, encoding="utf-8") as handle:
            return string.Template(handle.read()).substitute(mount=self.mount_unit(),
                                                             launcher=self.launcher)

    def install(self) -> str:
        """Write the unit file.

        Returns:
            Its path.
        """
        os.makedirs(self.unit_dir, exist_ok=True)
        path = os.path.join(self.unit_dir, self.NAME)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(self.render())
        return path
