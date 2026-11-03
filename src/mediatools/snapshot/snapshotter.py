"""Versioned backups with rsnapshot: checks, adoption of a mirror, runs."""
from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from datetime import datetime
from typing import Any

from mediatools.core.datadir import DataDir
from mediatools.core.prompt import Prompt
from mediatools.snapshot.rsnapconf import RsnapshotConf
from mediatools.snapshot.schedule import LevelSchedule
from mediatools.snapshot.settings import SnapshotSettings
from mediatools.snapshot.syncprobe import SyncProbe
from mediatools.sync.fscheck import FsCheck


class Snapshotter:
    """Drives rsnapshot on a removable backup disk.

    Commands (see the handbook for the procedure):

    * :meth:`check`  - read-only checks of configuration, disk and tools.
    * :meth:`adopt`  - one-time takeover of an existing mirror on the disk
      as the first snapshot, without copying data (``rename`` on the same
      file system), optionally kept permanently as ``stand0``.
    * :meth:`init`   - create an empty snapshot root for a fresh start.
    * :meth:`run`    - probe, sync with ``--delete`` into ``.sync`` and
      rotate the due levels.
    * :meth:`status` - list the snapshots.

    Every command that changes the disk first runs a :class:`SyncProbe`
    and refuses to continue if the result is implausible.

    Attributes:
        settings: Validated settings.
        log: Logger.
        prompt: Confirmation helper.
        conf: rsnapshot configuration.
        probe: Dry-run helper.
        schedule: Level schedule.
        fscheck: File-system helper.
        datadir: Data directory for reports.
        ismount: Mount-point test.
    """

    STAND0 = "stand0"
    SYNC = ".sync"
    GOOD_FS = frozenset({"ext4", "btrfs", "xfs"})

    def __init__(self, settings: SnapshotSettings, logger, prompt: Prompt,
                 conf: RsnapshotConf | None = None, probe: SyncProbe | None = None,
                 schedule: LevelSchedule | None = None, fscheck: FsCheck | None = None,
                 datadir: DataDir | None = None,
                 ismount: Callable[[str], bool] = os.path.ismount):
        """Wire the collaborators.

        Args:
            settings: Validated settings.
            logger: :class:`logging.Logger`.
            prompt: Confirmation helper.
            conf: rsnapshot configuration; built from settings if None.
            probe: Dry-run helper; built from settings if None.
            schedule: Level schedule; built from settings if None.
            fscheck: File-system helper; a new one if None.
            datadir: Data directory; the default one if None.
            ismount: Mount-point test (replaceable in tests).
        """
        self.ismount = ismount
        self.settings = settings
        self.log = logger
        self.prompt = prompt
        self.datadir = datadir or DataDir()
        self.conf = conf or RsnapshotConf(settings, self.datadir)
        self.probe = probe or SyncProbe(settings)
        self.schedule = schedule or LevelSchedule(settings, self.datadir)
        self.fscheck = fscheck or FsCheck()

    # -- helpers ---------------------------------------------------------
    def _path(self, name: str) -> str:
        """Return a path inside the snapshot root.

        Args:
            name: Entry name, e.g. ``alpha.0``.

        Returns:
            Absolute path.
        """
        return os.path.join(self.settings.root, name)

    def latest_mirror(self) -> str | None:
        """Return the newest copy of the source inside the snapshot root.

        Returns:
            Mirror inside ``.sync`` or ``alpha.0``; None if neither exists.
        """
        for name in (self.SYNC, "alpha.0"):
            mirror = self.settings.mirror(self._path(name))
            if os.path.isdir(mirror):
                return mirror
        return None

    def _write_list(self, prefix: str, paths: list[str]) -> str:
        """Write a list of relative paths to a report file.

        Args:
            prefix: File name prefix.
            paths: Lines to write.

        Returns:
            Path of the report.
        """
        report = self.datadir.file("reports", prefix, ".txt")
        with open(report, "w", encoding="utf-8", errors="surrogateescape") as handle:
            handle.writelines(path + "\n" for path in paths)
        return report

    def _log_probe(self, result: dict[str, Any], label: str) -> str:
        """Log a probe result and save the deletion list.

        Args:
            result: Result of :meth:`SyncProbe.run`.
            label: Report name prefix.

        Returns:
            Path of the deletion list.
        """
        report = self._write_list(label, result["deleted"])
        gib = 1024 ** 3
        self.log.info("probe: source %.1f GiB, to transfer %.2f GiB, new files %i, "
                      "changed %i, removed from mirror %i",
                      result["total_bytes"] / gib, result["transfer_bytes"] / gib,
                      result["new"], result["changed"], len(result["deleted"]))
        for path in result["deleted"][:10]:
            self.log.info("    would remove: %s", path)
        self.log.info("full list of removals: %s", report)
        return report

    @staticmethod
    def _top(directory: str) -> list[str]:
        """List the first entries of a directory for orientation.

        Args:
            directory: Directory to list.

        Returns:
            Up to 12 sorted entry names (``/`` appended to directories).
        """
        try:
            entries = sorted(os.scandir(directory), key=lambda e: e.name)
        except OSError:
            return []
        return [e.name + ("/" if e.is_dir(follow_symlinks=False) else "") for e in entries][:12]

    # -- check -----------------------------------------------------------
    def check(self) -> bool:
        """Check configuration, disk and tools; nothing is changed.

        Returns:
            True if a run or an adoption may proceed.
        """
        ok = True
        source, disk = self.settings.source, self.settings.disk
        if not os.path.isdir(source) or not os.listdir(source):
            self.log.error("source missing or empty: %s ([snapshot] source)", source)
            ok = False
        if not self.ismount(disk):
            self.log.error("not a mount point: %s ([snapshot] disk) - is the disk attached? "
                           "Mount points: lsblk -o NAME,FSTYPE,LABEL,MOUNTPOINTS", disk)
            return False
        fs_type = self.fscheck.fs_type(disk)
        if fs_type not in self.GOOD_FS:
            self.log.error("file system of %s is %s; ext4, btrfs or xfs required", disk, fs_type)
            ok = False
        if FsCheck.nested(source, disk):
            self.log.error("source and disk overlap: %s / %s", source, disk)
            ok = False
        for tool in ("rsnapshot", "rsync", "cp"):
            if not self._which(tool):
                self.log.error("%s is not installed (sudo apt install %s)", tool, tool)
                ok = False
        if ok:
            self.log.info("source %s | disk %s (%s, %.0f GiB free) | snapshot root %s",
                          source, disk, fs_type, FsCheck.free_bytes(disk) / 1024 ** 3,
                          self.settings.root)
            state = "missing" if not os.path.isdir(self.settings.root) else \
                ", ".join(self._top(self.settings.root)) or "empty"
            self.log.info("snapshot root content: %s", state)
        return ok

    def probe_report(self) -> bool:
        """Explain the state of the snapshot root and probe the newest mirror.

        Read-only; complements :meth:`check` and tells the user which
        command is expected next.

        Returns:
            True if the configuration is accepted and the newest mirror (if
            any) is plausibly a copy of the source.
        """
        if not os.path.isdir(self.settings.root):
            self.log.info("next step: either take over an existing copy on the disk with\n"
                          "  mediatools snapshot adopt <directory on the disk that "
                          "corresponds to %s>\nor start empty with\n"
                          "  mediatools snapshot init", self.settings.source)
            return True
        if not self._configtest():
            return False
        mirror = self.latest_mirror()
        if mirror is None:
            self.log.info("no snapshot yet; the first 'mediatools snapshot run' copies "
                          "the whole source")
            return True
        result = self.probe.run(mirror)
        self._log_probe(result, "snapshot_removals")
        if not SyncProbe.looks_like_copy(result):
            self.log.error("the newest snapshot %s does not match the source - check "
                           "[snapshot] source", mirror)
            return False
        return True

    @staticmethod
    def _which(tool: str) -> bool:
        """Tell whether an executable is on the PATH.

        Args:
            tool: Executable name.

        Returns:
            True if found.
        """
        return shutil.which(tool) is not None

    def _configtest(self) -> bool:
        """Write the rsnapshot configuration and test it.

        Returns:
            True if rsnapshot accepts it.
        """
        self.conf.write()
        ok, output = self.conf.configtest()
        (self.log.info if ok else self.log.error)("rsnapshot configtest: %s (%s)",
                                                  output, self.conf.path)
        return ok

    # -- init / adopt ----------------------------------------------------
    def init(self) -> int:
        """Create an empty snapshot root (fresh start, first run copies all).

        Returns:
            0 on success, 1 if checks fail or the root already exists.
        """
        if not self.check():
            return 1
        if os.path.exists(self.settings.root):
            self.log.error("snapshot root already exists: %s", self.settings.root)
            return 1
        os.makedirs(self.settings.root)
        self.log.info("created %s", self.settings.root)
        return 0 if self._configtest() else 1

    def adopt(self, existing: str, keep_stand0: bool = True) -> int:
        """Turn an existing mirror on the disk into the first snapshot.

        Nothing is copied: the directory is renamed into the layout
        rsnapshot expects (``.sync/localhost/<source path>``) and rotated to
        ``alpha.0``; ``stand0`` is a hard-link copy (``cp -al``) that is
        never rotated away and therefore keeps files that a later sync
        removes from the mirror.

        Args:
            existing: Directory on the disk that is a copy of the source.
            keep_stand0: Create the permanent ``stand0`` snapshot.

        Returns:
            0 on success, 1 if a check fails or the user declines.
        """
        existing = os.path.abspath(existing)
        if not (self.check() and self._adoptable(existing) and self._matches(existing)):
            return 1
        target = self.settings.mirror(self._path(self.SYNC))
        self.log.info("plan: rename %s -> %s, then rotate to alpha.0%s", existing, target,
                      " and keep a permanent copy as stand0" if keep_stand0 else "")
        if not self.prompt.yes_no("Adopt this directory as the first snapshot?"):
            return 1
        os.makedirs(os.path.dirname(target), exist_ok=True)
        os.rename(existing, target)
        self.log.info("renamed (no data copied)")
        if not self._configtest() or self.conf.run("alpha") not in (0, 2):
            self.log.error("rsnapshot failed; the data is in %s", target)
            return 1
        if keep_stand0:
            cp_tool = shutil.which("cp") or "/bin/cp"
            res = subprocess.run([cp_tool, "-al", self._path("alpha.0"),
                                  self._path(self.STAND0)], check=False)
            if res.returncode != 0:
                self.log.error("cp -al failed; alpha.0 is complete, stand0 is not")
                return 1
            self.log.info("stand0 created (hard links, no data copied)")
        return 0

    def _adoptable(self, existing: str) -> bool:
        """Check location and state for an adoption.

        Args:
            existing: Candidate directory.

        Returns:
            True if the directory is on the disk and the snapshot root is
            empty or missing.
        """
        if not os.path.isdir(existing):
            self.log.error("not a directory: %s", existing)
            return False
        if os.stat(existing).st_dev != os.stat(self.settings.disk).st_dev:
            self.log.error("%s is not on the backup disk %s; adoption only renames within "
                           "one file system", existing, self.settings.disk)
            return False
        if os.path.exists(self.settings.root) and os.listdir(self.settings.root):
            self.log.error("snapshot root %s is not empty; adoption is a one-time step",
                           self.settings.root)
            return False
        if FsCheck.nested(existing, self.settings.root):
            self.log.error("%s and the snapshot root overlap", existing)
            return False
        return True

    def _matches(self, existing: str) -> bool:
        """Probe whether a directory is a copy of the source.

        Args:
            existing: Candidate directory.

        Returns:
            True if the transfer volume is plausible; otherwise the expected
            and the found content are shown so the user can correct the path.
        """
        result = self.probe.run(existing)
        self._log_probe(result, "snapshot_adopt_removals")
        if result["returncode"] in (0, 24) and SyncProbe.looks_like_copy(result):
            return True
        self.log.error(
            "%s does not look like a copy of %s: %.1f of %.1f GiB would be transferred.\n"
            "  Expected: the directory on the disk whose content corresponds to the "
            "source itself.\n  source top level: %s\n  given top level:  %s\n"
            "  Correct the path (or [snapshot] source) and run the command again.",
            existing, self.settings.source, result["transfer_bytes"] / 1024 ** 3,
            result["total_bytes"] / 1024 ** 3, ", ".join(self._top(self.settings.source)),
            ", ".join(self._top(existing)))
        if result["stderr"]:
            self.log.error("rsync: %s", result["stderr"])
        return False

    # -- run -------------------------------------------------------------
    def run(self, dry_run: bool = False, now: datetime | None = None) -> int:
        """Probe, sync and rotate.

        Args:
            dry_run: Only probe and report.
            now: Current time (replaceable in tests).

        Returns:
            0 on success, 1 on refusal or error.
        """
        if not (self.check() and self._root_ready() and self._probe_acceptable()):
            return 1
        if dry_run:
            return 0
        if not self.prompt.yes_no("Start the snapshot run?", default=True):
            return 1
        if not self._configtest():
            return 1
        return self._sync_and_rotate(now or datetime.now())

    def _root_ready(self) -> bool:
        """Check that the snapshot root exists.

        Returns:
            True if it exists; otherwise the next step is explained.
        """
        if os.path.isdir(self.settings.root):
            return True
        self.log.error("snapshot root %s missing; run 'mediatools snapshot adopt PATH' "
                       "(existing mirror) or 'mediatools snapshot init' (fresh start)",
                       self.settings.root)
        return False

    def _probe_acceptable(self) -> bool:
        """Probe the sync and apply the space and deletion limits.

        Returns:
            True if the run may proceed.
        """
        mirror = self.latest_mirror() or self.settings.mirror(self._path(self.SYNC))
        result = self.probe.run(mirror)
        self._log_probe(result, "snapshot_removals")
        if result["returncode"] not in (0, 24):
            self.log.error("rsync probe failed: %s", result["stderr"])
            return False
        free = FsCheck.free_bytes(self.settings.disk)
        if result["transfer_bytes"] > 0.95 * free:
            self.log.error("not enough space: %.1f GiB to write, %.1f GiB free",
                           result["transfer_bytes"] / 1024 ** 3, free / 1024 ** 3)
            return False
        removals = len(result["deleted"])
        if removals <= self.settings.max_deletions:
            return True
        self.log.warning("%i files would leave the newest snapshot (limit %i, "
                         "[snapshot] max_deletions)", removals, self.settings.max_deletions)
        if not self.prompt.assume_yes and self.prompt.yes_no(
                "Continue anyway? (they stay in older snapshots)"):
            return True
        self.log.error("aborted because of the deletion limit")
        return False

    def _sync_and_rotate(self, now: datetime) -> int:
        """Run ``rsnapshot sync`` and rotate the due levels.

        Args:
            now: Current time.

        Returns:
            0 on success, 1 if rsnapshot reported a fatal error.
        """
        if self.conf.run("sync") not in (0, 2):
            self.log.error("rsnapshot sync failed; see %s", self.conf.log_file)
            return 1
        for level in self.schedule.due(now):
            before = self._inode(f"{level}.0")
            if self.conf.run(level) not in (0, 2):
                self.log.error("rsnapshot %s failed", level)
                return 1
            if self._inode(f"{level}.0") not in (None, before):
                self.schedule.record(level, now)
                self.log.info("rotated %s", level)
        if self.conf.run("alpha") not in (0, 2):
            self.log.error("rsnapshot alpha failed")
            return 1
        self.log.info("snapshot run complete")
        return 0

    def _inode(self, name: str) -> int | None:
        """Return the inode of an entry in the snapshot root.

        Args:
            name: Entry name.

        Returns:
            Inode number, or None if it does not exist.
        """
        try:
            return os.stat(self._path(name)).st_ino
        except FileNotFoundError:
            return None

    # -- status ----------------------------------------------------------
    def status(self) -> int:
        """Log the snapshots, their dates and the level schedule.

        Returns:
            0, or 1 if the snapshot root is not available.
        """
        root = self.settings.root
        if not os.path.isdir(root):
            self.log.error("snapshot root not available: %s (disk attached?)", root)
            return 1
        for entry in sorted(os.scandir(root), key=lambda e: e.stat().st_mtime, reverse=True):
            when = datetime.fromtimestamp(entry.stat().st_mtime)
            self.log.info("  %-10s %s", entry.name, when.strftime("%Y-%m-%d %H:%M"))
        self.log.info("free on disk: %.1f GiB", FsCheck.free_bytes(self.settings.disk) / 1024 ** 3)
        for level, when_text in sorted(self.schedule.load().items()):
            self.log.info("last %s rotation: %s", level, when_text)
        return 0
