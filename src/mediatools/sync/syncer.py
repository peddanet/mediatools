"""`mediatools sync` - hard-link preserving backup without deletions."""
from __future__ import annotations

import os
import shutil

from mediatools.core.datadir import DataDir
from mediatools.core.prompt import Prompt
from mediatools.sync.fscheck import FsCheck
from mediatools.sync.rsync_runner import RsyncRunner
from mediatools.sync.target_report import TargetReport


class Syncer:
    """Copies a media tree to a backup disk (or back) and reports leftovers.

    Steps:

    1. safety checks - rsync present, source and target not nested, target
       file system known to support hard links, practical hard-link test
    2. dry run - summary of new files, updates, hard links
    3. confirmation, then the real rsync run (see :class:`RsyncRunner`)
    4. report of files that exist only in the target (:class:`TargetReport`)

    The same command works in both directions: ``sync A B`` copies A to B,
    ``sync B A`` (or ``--reverse``) copies back. Because nothing is ever
    deleted, a reverse run also restores files that were removed on purpose
    in the other tree; the dry-run summary shows how many files would be
    created.

    Attributes:
        source: Absolute source directory.
        target: Absolute target directory.
        log: Logger.
        prompt: Prompt for the confirmation.
        datadir: Data directory for logs and reports.
        report: Write the target-only report after the transfer.
        all_duplicates: Include duplicate groups of the whole target.
        fs: File-system helper.
    """

    def __init__(self, source: str, target: str, logger, prompt: Prompt | None = None,
                 datadir: DataDir | None = None, report: bool = True,
                 all_duplicates: bool = False, fs: FsCheck | None = None):
        """Configure the synchronisation.

        Args:
            source: Source directory.
            target: Target directory (created if missing).
            logger: :class:`logging.Logger`.
            prompt: Prompt; a default one if None.
            datadir: Data directory; the default one if None.
            report: Write the target-only report after the transfer.
            all_duplicates: Include duplicate groups of the whole target.
            fs: File-system helper; a default one if None.
        """
        self.source = os.path.abspath(source)
        self.target = os.path.abspath(target)
        self.log = logger
        self.prompt = prompt or Prompt()
        self.datadir = datadir or DataDir()
        self.report = report
        self.all_duplicates = all_duplicates
        self.fs = fs or FsCheck()

    def check(self) -> bool:
        """Run all safety checks; the reason of a failure is logged.

        Returns:
            True if the transfer may start.
        """
        if shutil.which("rsync") is None:
            self.log.error("rsync not found - install it with: sudo apt install rsync")
            return False
        if not os.path.isdir(self.source):
            self.log.error("source is not a directory: %s", self.source)
            return False
        if FsCheck.nested(self.source, self.target):
            self.log.error("source and target must not contain each other")
            return False
        os.makedirs(self.target, exist_ok=True)
        fstype = self.fs.fs_type(self.target) or "unknown"
        self.log.info("source: %s (%s)", self.source, self.fs.fs_type(self.source))
        self.log.info("target: %s (%s)", self.target, fstype)
        if fstype in FsCheck.NO_HARDLINK_FS:
            self.log.error("target file system %s cannot store hard links - "
                           "every link would become a full copy", fstype)
            return False
        ok, why = FsCheck.can_hardlink(self.target)
        self.log.info("hard-link test in target: %s", why)
        return ok

    def run(self) -> int:
        """Execute checks, dry run, transfer and report.

        Returns:
            0 on success or when the user declined, 1 on error.
        """
        if not self.check():
            return 1
        runner = RsyncRunner(self.source, self.target)
        self.log.info("dry run: %s", " ".join(runner.command(dry_run=True)))
        summary = runner.dry_run()
        if summary["returncode"] not in (0, 24):
            self.log.error("rsync dry run failed (%s): %s",
                           summary["returncode"], summary["stderr"])
            return 1
        self.log.info("would copy new files: %i | update: %i | hard links: %i | "
                      "new directories: %i", summary["new"], summary["update"],
                      summary["hardlink"], summary["newdir"])
        for sample in summary["samples_new"]:
            self.log.info("    e.g. %s", sample)
        changes = summary["new"] + summary["update"] + summary["hardlink"] + summary["newdir"]
        if changes == 0:
            self.log.info("target is up to date")
        elif not self.prompt.yes_no("Start the transfer? (nothing will be deleted)"):
            self.log.info("aborted by user")
            return 0
        else:
            log_file = self.datadir.file("logs", "sync_rsync", ".log")
            code = runner.run(log_file)
            self.log.info("rsync finished with code %i, rsync log: %s", code, log_file)
            if code not in (0, 24):
                return 1
        if self.report:
            self.write_report()
        return 0

    def write_report(self) -> str:
        """Analyse the target-only files and write the TSV report.

        Returns:
            Path of the report file.
        """
        self.log.info("analysing files that exist only in the target ...")
        report = TargetReport(self.source, self.target, self.log)
        result = report.analyse(self.all_duplicates)
        path = self.datadir.file("reports", "sync_target_only", ".tsv")
        counts = TargetReport.write(result, path)
        self.log.info("target-only files: unique %i | duplicate %i (%.1f MiB) | "
                      "hardlinked %i", counts["unique"], counts["duplicate"],
                      counts["bytes_duplicate"] / 1024 ** 2, counts["hardlinked"])
        if self.all_duplicates:
            self.log.info("groups of identical files in different inodes: %i",
                          counts["dup_groups"])
        self.log.info("report: %s", path)
        return path
