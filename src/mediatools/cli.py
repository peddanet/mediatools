"""Command line interface: ``mediatools <subcommand> ...``."""
from __future__ import annotations

import argparse
import os
import shlex
import shutil
import sys

from mediatools.core.config import Config
from mediatools.core.datadir import DataDir
from mediatools.core.attachview import AttachView
from mediatools.core.codeccache import CodecCache
from mediatools.core.durationcache import DurationCache
from mediatools.core.mediatypes import MediaTypes
from mediatools.core.probe import MediaProbe
from mediatools.core.prompt import Prompt
from mediatools.core.runcontrol import RunControl
from mediatools.core.sleepinhibitor import SleepInhibitor
from mediatools.core.runlog import RunLog
from mediatools.core.versioninfo import VersionInfo
from mediatools.flatten import FlatPlan, Flattener
from mediatools.shrink import (DatabaseRenamer, HevcEncoder, ProgressReport, Shrinker,
                               ShrinkOptions)
from mediatools.shrink.resulttable import ResultTable
from mediatools.snapshot import SnapshotSettings, Snapshotter, SystemdTrigger
from mediatools.sync import Syncer, TargetPicker
from mediatools.triage import CandidateFinder, FileRemover, PlayerSession, Triage


class Cli:
    """Parses arguments and dispatches to the subcommand classes.

    Each ``cmd_*`` method builds the objects of one subcommand, runs it and
    returns the process exit code.

    Attributes:
        parser: The configured :class:`argparse.ArgumentParser`.
    """

    def __init__(self):
        """Build the argument parser."""
        self.parser = self._build_parser()

    # -- parser ----------------------------------------------------------
    @staticmethod
    def _build_parser() -> argparse.ArgumentParser:
        """Define all subcommands and options.

        Returns:
            The parser.
        """
        parser = argparse.ArgumentParser(
            prog="mediatools",
            description="Maintenance tools for large local media collections: "
                        "shrink videos to HEVC, review short clips, build flat "
                        "hard-link views and back up without losing hard links.")
        parser.add_argument("--version", action="store_true",
                            help="show the version and whether it is an installed release")
        sub = parser.add_subparsers(dest="command", metavar="<command>")

        cmd = sub.add_parser("shrink", help="re-encode videos to HEVC (libx265) in place")
        cmd.add_argument("path", nargs="?", help="directory (default: [collection] root)")
        cmd.add_argument("--keep-copies", action="store_true",
                         help="do not replace originals; keep shrunk copies in newShrinkedFiles/")
        cmd.add_argument("--foreground", action="store_true",
                         help="stay in this terminal; by default shrink moves into the "
                              "background after the confirmation (see 'mediatools attach')")
        cmd.add_argument("--crf", type=int, default=28, help="libx265 CRF (default 28)")
        cmd.add_argument("--threads", type=int, default=None,
                         help="CPU threads for decoding and x265 (default: logical CPUs - 1)")
        cmd.add_argument("--ext", default="mp4", help="video extensions (default mp4)")
        cmd.add_argument("--order", choices=ShrinkOptions.ORDERS, default="largest",
                         help="processing order (default: largest files first)")
        cmd.add_argument("--min-seconds", type=float, default=0.0,
                         help="leave out videos shorter than this (e.g. 60 before a triage)")
        cmd.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
        cmd.add_argument("-v", "--verbose", action="store_true", help="debug logging")

        sub.add_parser("stop", help="ask a running shrink (in another terminal) to stop "
                                    "after its current file; returns at once")
        sub.add_parser("attach", aliases=["front"],
                       help="watch a shrink running in the background (log and progress); "
                            "Ctrl+C leaves the view, the run continues")

        cmd = sub.add_parser("progress", help="read-only report: which videos are shrunk")
        cmd.add_argument("path", nargs="?", help="directory (default: [collection] root)")
        cmd.add_argument("--ext", default="mp4")

        cmd = sub.add_parser("triage", help="review short videos: close a window = remove, "
                                            "leave it open = keep")
        cmd.add_argument("path", nargs="?", help="directory (default: [collection] root)")
        cmd.add_argument("--max-seconds", type=float, default=60.0)
        cmd.add_argument("--min-seconds", type=float, default=0.0)
        cmd.add_argument("--batch", type=int, default=10)
        cmd.add_argument("--ext", default="mp4,m4v,mov,mkv,webm")
        cmd.add_argument("--player", default="vlc")
        cmd.add_argument("--audio", action="store_true", help="play with sound")
        cmd.add_argument("--delete", action="store_true", help="delete instead of trash")
        cmd.add_argument("--no-placeholder", action="store_true",
                         help="do not leave an empty file under the name of a removed video")
        cmd.add_argument("--include-kept", action="store_true")
        cmd.add_argument("--dry-run", action="store_true",
                         help="only count and list candidates, estimate the review time")
        cmd.add_argument("-y", "--yes", action="store_true",
                         help="skip the start question (each batch is still confirmed)")

        cmd = sub.add_parser("flatten", help="hard links of all media of each unit in <unit>/_flat")
        cmd.add_argument("path", nargs="?",
                         help="master directory (default: [collection] root); "
                              "its sub-directories are units")
        cmd.add_argument("--ext", default=None, help="extensions (default: video, image, audio)")
        cmd.add_argument("--no-abbrev", action="store_true", help="never abbreviate folder names")
        cmd.add_argument("--prune", action="store_true",
                         help="also remove links whose source is gone")
        cmd.add_argument("--dry-run", action="store_true", help="analyse and save plan only")
        cmd.add_argument("--undo", metavar="PLAN", help="remove the links created by a plan file")
        cmd.add_argument("-y", "--yes", action="store_true")

        cmd = sub.add_parser("sync", help="backup with hard links preserved, never deletes")
        cmd.add_argument("source", nargs="?", help="default: [collection] root")
        cmd.add_argument("target", nargs="?", help="omit to choose the disk interactively")
        cmd.add_argument("--reverse", action="store_true", help="swap source and target")
        cmd.add_argument("--no-report", action="store_true", help="skip target-only report")
        cmd.add_argument("--find-duplicates", action="store_true",
                         help="also list identical files in different inodes on the target")
        cmd.add_argument("--report-only", action="store_true", help="no transfer, report only")
        cmd.add_argument("-y", "--yes", action="store_true")
        Cli._add_admin_parsers(sub)
        return parser

    @staticmethod
    def _add_admin_parsers(sub) -> None:
        """Define the subcommands rename-db, config and snapshot.

        Args:
            sub: Sub-parser collection of the main parser.
        """
        cmd = sub.add_parser("rename-db", help="rename shrink databases OLD -> NEW "
                                               "(NEW is stored as [shrink] db_name)")
        cmd.add_argument("old", help="current database file name")
        cmd.add_argument("new", help="new database file name")
        cmd.add_argument("path", nargs="?", help="directory (default: [collection] root)")
        cmd.add_argument("--dry-run", action="store_true", help="only list what would change")

        cmd = sub.add_parser("config", help="show, create or locate the configuration file")
        group = cmd.add_mutually_exclusive_group()
        group.add_argument("--init", action="store_true", help="write a commented template")
        group.add_argument("--path", action="store_true", help="print the file location only")
        group.add_argument("--set", metavar="SECTION.KEY=VALUE", action="append",
                           help="change a value, e.g. --set collection.root=~/Videos "
                                "(repeatable)")

        cmd = sub.add_parser("snapshot", help="versioned backups with rsnapshot")
        cmd.add_argument("action", choices=["check", "adopt", "init", "run", "status",
                                            "install-trigger"])
        cmd.add_argument("existing", nargs="?", help="adopt: existing mirror on the disk")
        cmd.add_argument("--no-stand0", action="store_true",
                         help="adopt: do not keep a permanent copy of the first state")
        cmd.add_argument("--dry-run", action="store_true", help="run: probe only")
        cmd.add_argument("-y", "--yes", action="store_true",
                         help="run: no questions; abort instead of asking")

    # -- dispatch --------------------------------------------------------
    def main(self, argv: list[str] | None = None) -> int:
        """Parse arguments and run the selected subcommand.

        Args:
            argv: Arguments without the program name; ``sys.argv`` if None.

        Returns:
            Process exit code.
        """
        args, extra = self.parser.parse_known_args(argv)
        # argparse cannot assign an optional positional that follows an
        # option ("rename-db OLD --dry-run PATH"); accept exactly that case
        for name in ("path", "source"):
            if len(extra) == 1 and not extra[0].startswith("-") \
                    and getattr(args, name, "") is None:
                setattr(args, name, extra.pop())
        if extra:
            self.parser.error("unrecognized arguments: " + " ".join(extra))
        if args.version:
            # printed directly: argparse would wrap a long checkout path
            print(f"mediatools {VersionInfo().describe()}")
            return 0
        if not args.command:
            self.parser.print_help()
            return 1
        return getattr(self, "cmd_" + args.command.replace("-", "_"))(args)

    @staticmethod
    def _dir(path: str) -> str:
        """Validate a directory argument.

        Args:
            path: Directory given on the command line.

        Returns:
            Absolute path.

        Raises:
            SystemExit: If the path is not a directory.
        """
        if not os.path.isdir(path):
            raise SystemExit(f"Error: not a directory: {path}")
        return os.path.abspath(path)

    @staticmethod
    def _need(*tools: str) -> None:
        """Ensure external programs are installed.

        Args:
            *tools: Executable names.

        Raises:
            SystemExit: If at least one tool is missing.
        """
        missing = [tool for tool in tools if shutil.which(tool) is None]
        if missing:
            raise SystemExit("Error: required tool(s) not found: " + ", ".join(missing))

    # -- subcommands -----------------------------------------------------
    def cmd_shrink(self, args: argparse.Namespace) -> int:
        """Run ``mediatools shrink``.

        Args:
            args: Parsed arguments.

        Returns:
            Exit code of :meth:`Shrinker.run` (0 if the user declined).
        """
        config = Config()
        root = self._dir(config.require_path("collection", "root", args.path))
        self._need("ffmpeg", "ffprobe")
        mode = "keep shrunk copies" if args.keep_copies else "REPLACE the originals"
        run = RunLog("shrink", args.verbose)
        probe = MediaProbe()
        encoder = HevcEncoder(args.crf, args.threads, probe, run.logger)
        durations = DurationCache(os.path.join(DataDir().sub("state"), "triage_durations.tsv"))
        opts = ShrinkOptions(args.keep_copies, MediaTypes.parse_extensions(args.ext),
                             config.db_name(), args.order, args.min_seconds)
        codecs = CodecCache(os.path.join(DataDir().sub("state"), "shrink_codecs.tsv"))
        shrinker = Shrinker(root, run.logger, opts, encoder, probe, durations, codecs=codecs)
        shrinker.control = RunControl(
            "shrink", background=not args.foreground, log_path=run.path,
            inhibitor=SleepInhibitor("mediatools shrink", run.logger))
        shrinker.state["results"] = ResultTable(
            root, DataDir().file("reports", "shrink_results", ".txt"), run.logger,
            shutil.get_terminal_size().columns - 1)
        shrinker.install_signal_handlers()
        prompt = Prompt(args.yes)
        return shrinker.run(lambda: prompt.yes_no(f"Start encoding and {mode}?"))

    @staticmethod
    def cmd_stop(_args: argparse.Namespace) -> int:
        """Run ``mediatools stop``: request a stop of the running shrink.

        Returns:
            0 if a running shrink was asked to stop, 1 if none runs.
        """
        pid = RunControl("shrink").request_stop()
        if pid is None:
            print("no shrink run is active - nothing to stop")
            return 1
        print(f"stop requested: shrink (pid {pid}) finishes its current file and ends")
        return 0

    @staticmethod
    def cmd_attach(_args: argparse.Namespace) -> int:
        """Run ``mediatools attach`` (alias ``front``): watch a background shrink.

        Returns:
            0 after showing a run, 1 if none runs.
        """
        return AttachView(RunControl("shrink")).run()

    cmd_front = cmd_attach                       # alias of 'attach'

    def cmd_progress(self, args: argparse.Namespace) -> int:
        """Run ``mediatools progress``.

        Args:
            args: Parsed arguments.

        Returns:
            Exit code of :meth:`ProgressReport.run`.
        """
        config = Config()
        root = self._dir(config.require_path("collection", "root", args.path))
        run = RunLog("progress")
        return ProgressReport(root, run.logger, extensions=MediaTypes.parse_extensions(args.ext),
                              db_name=config.db_name()).run()

    def cmd_triage(self, args: argparse.Namespace) -> int:
        """Run ``mediatools triage``.

        Args:
            args: Parsed arguments.

        Returns:
            Exit code of :meth:`Triage.run` (0 for a dry run).

        Raises:
            SystemExit: If the duration range is invalid.
        """
        root = self._dir(Config().require_path("collection", "root", args.path))
        if args.min_seconds > args.max_seconds:
            raise SystemExit("Error: --min-seconds is larger than --max-seconds")
        self._need("ffprobe")
        run = RunLog("triage")
        kept = Triage.kept_list()
        cache = DurationCache(os.path.join(DataDir().sub("state"), "triage_durations.tsv"))
        finder = CandidateFinder(root, MediaTypes.parse_extensions(args.ext), args.min_seconds,
                                 args.max_seconds, None if args.include_kept else kept,
                                 logger=run.logger, cache=cache)
        run.logger.info("counting matching videos (durations cached in %s) ...", cache.path)
        candidates = finder.collect()
        triage = Triage(root, run.logger, candidates, PlayerSession(args.player, args.audio),
                        FileRemover(args.delete, run.logger), kept, args.batch,
                        placeholder=not args.no_placeholder)
        if args.dry_run:
            for path, duration in candidates:
                run.logger.info("  %6.1fs  %s", duration, path)
            triage.announce(candidates, finder.scanned, ask=False)
            run.logger.info("dry run - nothing opened, nothing removed")
            return 0
        self._need(shlex.split(args.player)[0], *([] if args.delete else ["gio"]))
        # -y only skips this start question; every batch is still confirmed
        if not triage.announce(candidates, finder.scanned, ask=not args.yes):
            return 0
        triage.install_signal_handlers()
        return triage.run()

    def cmd_flatten(self, args: argparse.Namespace) -> int:
        """Run ``mediatools flatten`` (analysis, plan, confirmation, apply).

        Args:
            args: Parsed arguments.

        Returns:
            0 on success or dry run, 1 if a link could not be created.

        Raises:
            SystemExit: If no path is given and none is configured.
        """
        run = RunLog("flatten")
        if args.undo:
            removed = Flattener(os.getcwd(), run.logger).undo(FlatPlan.load(args.undo))
            run.logger.info("removed %i links of plan %s", removed, args.undo)
            return 0
        config = Config()
        root = self._dir(config.require_path("collection", "root", args.path))
        extensions = MediaTypes.parse_extensions(args.ext) if args.ext else MediaTypes.MEDIA
        flattener = Flattener(root, run.logger, extensions, not args.no_abbrev, args.prune,
                              config.abbreviations())
        run.logger.info("master: %s  units: %i  name limit: %i bytes",
                        root, len(flattener.units()), flattener.name_limit(root))
        plan = flattener.analyse()
        plan_path = DataDir().file("plans", "flatten", ".tsv")
        plan.save(plan_path)
        todo = self._log_plan(run.logger, plan, plan_path)
        if args.dry_run or todo == 0:
            return 0
        if not Prompt(args.yes).yes_no(f"Apply {todo} change(s) inside the _flat directories?"):
            return 0
        done = flattener.apply(plan)
        run.logger.info("done: %s  (undo: mediatools flatten --undo %s)", done, plan_path)
        return 1 if done["error"] else 0

    @staticmethod
    def _log_plan(logger, plan: FlatPlan, plan_path: str) -> int:
        """Log the summary of a flatten plan.

        Args:
            logger: :class:`logging.Logger`.
            plan: Plan from :meth:`Flattener.analyse`.
            plan_path: Where the plan was saved.

        Returns:
            Number of changes the plan would make.
        """
        counts, levels = plan.counts(), plan.level_counts()
        logger.info("new links: %i | renewed: %i | already present: %i | prune: %i | "
                    "not possible: %i", counts.get("link", 0), counts.get("relink", 0),
                    counts.get("ok", 0), counts.get("prune", 0), counts.get("skip", 0))
        logger.info("naming levels of new links: %s (0 full, 1 abbreviated, "
                    "2 stem cut, 3 hashed dir)", dict(sorted(levels.items())))
        shortened = [e for e in plan.entries if e.action in ("link", "relink") and e.level >= 2]
        for entry in shortened[:10]:
            logger.info("    shortened (level %i): %s", entry.level, os.path.basename(entry.link))
        for entry in plan.by_action("skip")[:10]:
            logger.info("    not possible: %s (%s)", entry.source, entry.note)
        logger.info("plan: %s", plan_path)
        return counts.get("link", 0) + counts.get("relink", 0) + counts.get("prune", 0)

    def cmd_sync(self, args: argparse.Namespace) -> int:
        """Run ``mediatools sync``.

        Args:
            args: Parsed arguments.

        Returns:
            Exit code of :meth:`Syncer.run`; 1 if no target was chosen.
        """
        prompt = Prompt(args.yes)
        config = Config()
        if not args.source and config.path_value("sync", "source"):
            args.source = config.path_value("sync", "source")
        args.source = config.require_path("collection", "root", args.source)
        picked = args.target
        if picked is None:
            picked = TargetPicker(prompt).pick(args.source)
            if not picked:
                return 1
        source, target = (picked, args.source) if args.reverse else (args.source, picked)
        run = RunLog("sync")
        syncer = Syncer(source, target, run.logger, prompt, report=not args.no_report,
                        all_duplicates=args.find_duplicates)
        if args.report_only:
            syncer.write_report()
            return 0
        code = syncer.run()
        if code == 0 and args.target is None:
            TargetPicker(prompt).remember(args.source, picked)
        return code

    def cmd_rename_db(self, args: argparse.Namespace) -> int:
        """Run ``mediatools rename-db``.

        Args:
            args: Parsed arguments.

        Returns:
            0 on success or dry run, 1 if a file could not be renamed.
        """
        config = Config()
        root = self._dir(config.require_path("collection", "root", args.path))
        run = RunLog("rename-db")
        renamer = DatabaseRenamer(root, args.old, args.new, run.logger)
        plan = renamer.plan()
        conflicts = [src for src, _dst, status in plan if status == "conflict"]
        run.logger.info("%i file(s) %r -> %r below %s, %i conflict(s)",
                        len(plan) - len(conflicts), args.old, renamer.new, root, len(conflicts))
        for path in conflicts[:10]:
            run.logger.warning("    target name exists, left alone: %s", path)
        if args.dry_run:
            run.logger.info("dry run - nothing renamed, configuration unchanged")
            return 0
        counts = renamer.apply(plan)
        run.logger.info("done: %s", counts)
        if args.new != config.db_name():
            config.set_value("shrink", "db_name", args.new)
            run.logger.info("[shrink] db_name = %s stored in %s", args.new, config.path)
        return 1 if counts["error"] else 0

    @staticmethod
    def cmd_config(args: argparse.Namespace) -> int:
        """Run ``mediatools config``.

        Args:
            args: Parsed arguments.

        Returns:
            0 on success.

        Raises:
            SystemExit: If a ``--set`` item is not ``SECTION.KEY=VALUE``.
        """
        config = Config()
        if args.path:
            print(config.path)
        elif args.init:
            written = config.write_template()
            print(("created " if written else "already exists, not changed: ") + config.path)
        elif args.set:
            for item in args.set:
                target, sep, value = item.partition("=")
                section, dot, key = target.strip().partition(".")
                if not sep or not dot or not section or not key:
                    raise SystemExit(f"Error: --set expects SECTION.KEY=VALUE, got {item!r}")
                config.set_value(section, key.strip(), value.strip())
                print(f"[{section}] {key.strip()} = {value.strip()}   ({config.path})")
        else:
            state = "" if config.exists() else "   (file missing - defaults shown; " \
                                               "create it with --init)"
            print(f"# {config.path}{state}\n")
            print(config.render())
        return 0

    def cmd_snapshot(self, args: argparse.Namespace) -> int:
        """Run ``mediatools snapshot <action>``.

        Args:
            args: Parsed arguments.

        Returns:
            Exit code of the action.

        Raises:
            SystemExit: If ``adopt`` is called without a directory.
        """
        settings = SnapshotSettings.from_config(Config())
        run = RunLog("snapshot")
        prompt = Prompt(args.yes)
        snap = Snapshotter(settings, run.logger, prompt)
        if args.action == "check":
            ok = snap.check() and snap.probe_report()
            return 0 if ok else 1
        if args.action == "adopt":
            if not args.existing:
                raise SystemExit("Error: adopt needs the existing mirror on the disk, e.g.\n"
                                 "  mediatools snapshot adopt /media/USER/BACKUP/collection")
            return snap.adopt(args.existing, keep_stand0=not args.no_stand0)
        if args.action == "init":
            return snap.init()
        if args.action == "run":
            return snap.run(dry_run=args.dry_run)
        if args.action == "status":
            return snap.status()
        return self._install_trigger(settings, run.logger)

    @staticmethod
    def _install_trigger(settings: SnapshotSettings, logger) -> int:
        """Install the systemd user unit after checking the mount unit.

        Args:
            settings: Validated snapshot settings.
            logger: :class:`logging.Logger`.

        Returns:
            0 on success, 1 if the disk's mount unit is not known.
        """
        launcher = shutil.which("mediatools") or os.path.abspath(sys.argv[0])
        trigger = SystemdTrigger(settings.disk, launcher)
        if not trigger.mount_unit_known():
            logger.error("systemd does not report %s as active - attach and mount the disk "
                         "first, then repeat", trigger.mount_unit())
            return 1
        path = trigger.install()
        logger.info("written: %s\nactivate with (normal user):\n"
                    "  systemctl --user daemon-reload\n"
                    "  systemctl --user enable %s", path, trigger.NAME)
        return 0


def main(argv: list[str] | None = None) -> int:
    """Console-script entry point.

    Args:
        argv: Arguments without the program name; ``sys.argv`` if None.

    Returns:
        Process exit code.
    """
    return Cli().main(argv)


if __name__ == "__main__":
    sys.exit(main())
