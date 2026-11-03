"""Read-only rsync dry run that predicts the effect of a snapshot sync."""
from __future__ import annotations

import re
import subprocess
from typing import Any

from mediatools.snapshot.settings import SnapshotSettings


class SyncProbe:
    """Compares the source with a mirror without changing anything.

    The dry run uses the same rsync options as rsnapshot (``-aH --delete
    --no-inc-recursive`` plus the excludes) and reports what a real sync
    would do. It answers the two questions that decide whether a run is
    safe: *is this mirror really a copy of this source?* (transfer volume)
    and *how much would disappear from it?* (deletions).

    Attributes:
        settings: Validated settings.
        rsync: rsync executable.
    """

    TOTAL = re.compile(r"^Total file size: (\d+) bytes", re.M)
    TRANSFER = re.compile(r"^Total transferred file size: (\d+) bytes", re.M)

    def __init__(self, settings: SnapshotSettings, rsync: str = "rsync"):
        """Configure the probe.

        Args:
            settings: Validated settings.
            rsync: rsync executable.
        """
        self.settings = settings
        self.rsync = rsync

    def command(self, mirror: str) -> list[str]:
        """Build the dry-run command line.

        Args:
            mirror: Existing copy of the source.

        Returns:
            Argument list.
        """
        cmd = [self.rsync, "-aHn", "--delete", "--delete-excluded", "--numeric-ids",
               "--no-inc-recursive", "--stats", "--no-human-readable", "--itemize-changes"]
        cmd += [f"--exclude={pattern}" for pattern in self.settings.exclude]
        return cmd + [self.settings.source.rstrip("/") + "/", mirror.rstrip("/") + "/"]

    def run(self, mirror: str) -> dict[str, Any]:
        """Run the dry run against a mirror.

        Args:
            mirror: Existing copy of the source.

        Returns:
            See :meth:`parse`, plus ``returncode`` and ``stderr``.
        """
        res = subprocess.run(self.command(mirror), capture_output=True, text=True,
                             errors="surrogateescape", check=False)
        result = self.parse(res.stdout)
        result["returncode"] = res.returncode
        result["stderr"] = res.stderr.strip()
        return result

    @classmethod
    def parse(cls, output: str) -> dict[str, Any]:
        """Evaluate itemized output and statistics.

        Args:
            output: stdout of the dry run.

        Returns:
            ``deleted`` (list of relative paths), ``new`` and ``changed``
            (file counts), ``total_bytes`` (source size) and
            ``transfer_bytes`` (data a real sync would write).
        """
        deleted: list[str] = []
        new = changed = 0
        for line in output.splitlines():
            if line.startswith("*deleting "):
                deleted.append(line[len("*deleting "):].strip())
            elif line.startswith(">f+++"):
                new += 1
            elif line.startswith(">f"):
                changed += 1
        total = cls.TOTAL.search(output)
        transfer = cls.TRANSFER.search(output)
        return {"deleted": deleted, "new": new, "changed": changed,
                "total_bytes": int(total.group(1)) if total else 0,
                "transfer_bytes": int(transfer.group(1)) if transfer else 0}

    @staticmethod
    def looks_like_copy(result: dict[str, Any], tolerance: float = 0.25) -> bool:
        """Tell whether the mirror is plausibly a copy of the source.

        If more than ``tolerance`` of the source volume would have to be
        transferred, the mirror is most likely a different directory (wrong
        path or wrong level) and must not be adopted.

        Args:
            result: Result of :meth:`run`.
            tolerance: Accepted share of data to transfer.

        Returns:
            True if the transfer volume is within the tolerance.
        """
        total = result["total_bytes"]
        return total == 0 or result["transfer_bytes"] <= tolerance * total
