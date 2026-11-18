"""The command ``mediatools broken``: collect, check the backup, review, list."""
from __future__ import annotations

import argparse
import os
import shlex
import sys
from typing import Callable

from mediatools.broken.assessor import Assessor
from mediatools.broken.backup import BackupMirror
from mediatools.broken.collector import BrokenCollector
from mediatools.broken.downloaderdb import DownloaderDatabase
from mediatools.broken.finder import BrokenFinder
from mediatools.broken.quarantine import Quarantine
from mediatools.broken.repairer import RepairEncoder
from mediatools.broken.review import BrokenReview
from mediatools.core.config import Config
from mediatools.core.decodecheck import DecodeCheck
from mediatools.core.prompt import Prompt
from mediatools.core.runlog import RunLog
from mediatools.triage.player import PlayerSession
from mediatools.triage.remover import FileRemover


class BrokenCommand:
    """Carries out ``mediatools broken`` with its options.

    Order: ``--collect`` (measure, set aside, check the backup),
    ``--backup-check`` (check every set-aside video again), ``--review``
    (decide video by video, in a terminal or in the GUI). Without any of
    them the set-aside videos are listed with a hint.

    Attributes:
        config: Configuration.
        root: Shrink root.
        need: Checks that programs exist (raises SystemExit otherwise).
        preset: x265 preset for repairs.
        runlog: Log of this run.
        quarantine: The ``_broken`` directory of the root.
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
        self.quarantine = Quarantine(root, self.runlog.logger, config.db_name())
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
        if args.collect or args.backup_check or args.review:
            self.need("ffmpeg", "ffprobe")
        if args.collect:
            finder = BrokenFinder(self.root, self.config.db_name(), args.report)
            collector = BrokenCollector(self.quarantine, finder, Assessor(), self.backup,
                                        Prompt(args.yes))
            code = collector.run(dry_run=args.dry_run, ask=not args.yes)
            if code or args.dry_run or not (args.review or args.backup_check):
                return code
        if args.backup_check:
            reason = self.quarantine.check_backup(self.backup, self.quarantine.open_items())
            if reason is not None:
                self.runlog.logger.error("backup check not possible: %s", reason)
                return 1
            if not args.review:
                return 0
        if args.review:
            return self.review(args)
        return self.listing()

    def listing(self) -> int:
        """List the set-aside videos with a hint how to go on.

        Returns:
            0.
        """
        items = self.quarantine.open_items()
        log = self.runlog.logger
        for item in items:
            log.info("  %-9s %s  backup: %s  %s", item.kind, item.assessment.summary(),
                     item.backup.state if item.backup else "not checked",
                     os.path.relpath(item.original, self.root))
        log.info("%i video(s) set aside in %s", len(items), self.quarantine.directory)
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
        review = BrokenReview(self.quarantine,
                              PlayerSession(args.player, not args.mute, volume=volume),
                              FileRemover(args.delete, log), repairer,
                              DownloaderDatabase(self.config.get("broken", "downloader_db"), log),
                              self.backup)
        return review.run()
