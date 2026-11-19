"""The command ``mediatools broken``: collect, check the backup, review, list."""
from __future__ import annotations

import argparse
import os
import shlex
import sys
from typing import Callable

from mediatools.broken.assessmentstore import AssessmentStore
from mediatools.broken.assessor import Assessor
from mediatools.broken.backup import BackupMirror
from mediatools.broken.collector import BrokenCollector
from mediatools.broken.downloaderdb import DownloaderDatabase
from mediatools.broken.finder import BrokenFinder
from mediatools.broken.quarantine import Quarantine
from mediatools.broken.quarantineset import QuarantineSet
from mediatools.broken.repairer import RepairEncoder
from mediatools.broken.review import BrokenReview
from mediatools.core.config import Config
from mediatools.core.datadir import DataDir
from mediatools.core.decodecheck import DecodeCheck
from mediatools.core.prompt import Prompt
from mediatools.core.runlog import RunLog
from mediatools.triage.player import PlayerSession
from mediatools.triage.remover import FileRemover


class BrokenCommand:
    """Carries out ``mediatools broken`` with its options.

    Order: ``--collect`` (measure, set aside, check the backup),
    ``--backup-check`` (check the set-aside videos that need it),
    ``--review`` (decide video by video, in a terminal or in the GUI).
    Without any of them the set-aside videos are listed with a hint. Every
    action covers the ``_broken`` directory of the root and those found
    below it (:class:`QuarantineSet`).

    Attributes:
        config: Configuration.
        root: Shrink root.
        need: Checks that programs exist (raises SystemExit otherwise).
        preset: x265 preset for repairs.
        runlog: Log of this run.
        quarantine: The ``_broken`` directory of the root.
        quarantines: It and every ``_broken`` directory below the root.
        backup: Backup mirror (``[broken] backup``, ``backup_of``).
    """

    VOLUME = 30

    def __init__(self, config: Config, root: str, need: Callable[..., None], preset: str):
        """Prepare the run.

        Args:
            config: Configuration.
            root: Shrink root.
            need: Checks that programs exist.
            preset: x265 preset for repairs.
        """
        self.config = config
        self.root = root
        self.need = need
        self.preset = preset
        self.runlog = RunLog("broken")
        self.quarantines = QuarantineSet(root, self.runlog.logger, config.db_name())
        self.quarantine: Quarantine = self.quarantines.primary
        self.backup = BackupMirror(config.path_value("broken", "backup"),
                                   config.path_value("broken", "backup_of")
                                   or os.path.expanduser("~"), self.runlog.logger)

    def run(self, args: argparse.Namespace) -> int:
        """Run the command.

        Args:
            args: Parsed arguments.

        Returns:
            Exit code.

        Raises:
            SystemExit: If ``--dry-run`` is given without ``--collect``.
        """
        if args.dry_run and not args.collect:
            raise SystemExit("Error: --dry-run works only with --collect")
        self.describe()
        if args.collect or args.backup_check or args.review:
            self.need("ffmpeg", "ffprobe")
        recheck = bool(getattr(args, "recheck", False))
        if args.collect:
            finder = BrokenFinder(self.root, self.config.db_name(), args.report)
            store = AssessmentStore(os.path.join(DataDir().sub("state"), AssessmentStore.FILE),
                                    self.runlog.logger, recheck)
            collector = BrokenCollector(self.quarantine, finder, Assessor(), self.backup,
                                        Prompt(args.yes), quarantines=self.quarantines,
                                        store=store, recheck=recheck)
            code = collector.run(dry_run=args.dry_run, ask=not args.yes)
            if args.dry_run and (args.review or args.backup_check):
                self.runlog.logger.warning(
                    "dry run: %s not carried out - run it without --dry-run",
                    " and ".join(name for name, given in (("--backup-check", args.backup_check),
                                                          ("--review", args.review)) if given))
            if code or args.dry_run or not (args.review or args.backup_check):
                return code
        if args.backup_check:
            code = self.backup_check(recheck)
            if code or not args.review:
                return code
        if args.review:
            return self.review(args)
        return self.listing()

    def describe(self) -> None:
        """Log the quarantines found and the state of the backup."""
        log = self.runlog.logger
        for line in self.quarantines.describe():
            log.info("_broken folder %s", line)
        reason = self.backup.unavailable(self.root)
        log.info("backup: %s", reason or f"{self.backup.backup} available")

    def backup_check(self, recheck: bool) -> int:
        """Check the backup for the set-aside videos of all quarantines that need it.

        Args:
            recheck: Check also videos that have a result.

        Returns:
            0, or 1 if the backup is not available.
        """
        reason = self.backup.unavailable(self.root)
        if reason is not None:
            self.runlog.logger.error("backup check not possible: %s", reason)
            return 1
        helper = BrokenCollector(self.quarantine, BrokenFinder(self.root, self.config.db_name()),
                                 Assessor(), self.backup, quarantines=self.quarantines,
                                 recheck=recheck)
        pending = [item for _member, item in self.quarantines.open_items()
                   if helper.needs_backup_check(item)]
        if not pending:
            self.runlog.logger.info("backup check: every set-aside video already has a result "
                                    "(--recheck checks them again)")
            return 0
        helper.check_backup()
        return 0

    def listing(self) -> int:
        """List the set-aside videos of all quarantines with a hint how to go on.

        Returns:
            0.
        """
        log = self.runlog.logger
        pairs = self.quarantines.open_items()
        for member, item in pairs:
            log.info("  %-9s %s  backup: %s  %s", item.kind, item.assessment.summary(),
                     item.backup.state if item.backup else "not checked",
                     os.path.relpath(item.original, self.root))
            del member
        log.info("%i video(s) set aside in %i _broken folder(s)", len(pairs),
                 len(self.quarantines.members))
        log.info("nothing measured or moved: --collect measures and sets videos aside, "
                 "--review goes through them")
        return 0

    def review(self, args: argparse.Namespace) -> int:
        """Go through the set-aside videos.

        Args:
            args: Parsed arguments.

        Returns:
            Exit code of the review.

        Raises:
            SystemExit: If it runs neither in a terminal nor in the GUI.
        """
        if not sys.stdin.isatty() and os.environ.get(Prompt.GUI_ENV) != "1":
            raise SystemExit("Error: broken --review asks for every video; start it in a "
                             "terminal or in the GUI")
        self.need(shlex.split(args.player)[0], *([] if args.delete else ["gio"]))
        log = self.runlog.logger
        volume = self.config.integer("broken", "volume") \
            if self.config.get("broken", "volume") else self.VOLUME
        repairer = RepairEncoder(logger=log, decode=DecodeCheck(), preset=self.preset)
        player = PlayerSession(args.player, not args.mute, volume=volume)
        downloader = DownloaderDatabase(self.config.get("broken", "downloader_db"), log)
        for member in self.quarantines.members:
            if not member.open_items():
                continue
            log.info("reviewing %s", os.path.relpath(member.directory, self.root))
            review = BrokenReview(member, player, FileRemover(args.delete, log), repairer,
                                  downloader, self.backup)
            code = review.run()
            if code or review.stopped:
                return code
        return 0
