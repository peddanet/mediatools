"""Command line interface: ``mediatools <subcommand> ...``."""
from __future__ import annotations

import argparse
import os
import shlex
import shutil
import sys

from mediatools.core.config import Config
from mediatools.core.datadir import DataDir
from mediatools.core.datamigration import DataMigration
from mediatools.core.decodecheck import DecodeCheck
from mediatools.core.attachview import AttachView
from mediatools.core.codeccache import CodecCache
from mediatools.core.durationcache import DurationCache
from mediatools.core.mediatypes import MediaTypes
from mediatools.core.placesreport import PlacesReport
from mediatools.core.privacylists import PrivacyLists
from mediatools.core.probe import MediaProbe
from mediatools.core.prompt import Prompt
from mediatools.core.runcontrol import RunControl
from mediatools.core.runstatus import RunStatus
from mediatools.core.sleepinhibitor import SleepInhibitor
from mediatools.core.runlog import RunLog
from mediatools.core.selfrestart import SelfRestart
from mediatools.core.versioninfo import VersionInfo
from mediatools.core.progressbase import ProgressBase
from mediatools.flatten import FlatPlan, Flattener
from mediatools.gui.desktopentry import DesktopEntry
from mediatools.gui.launcher import GuiLauncher
from mediatools.shrink import (DatabaseRenamer, HevcEncoder, ProgressReport, Shrinker,
                               ShrinkOptions)
from mediatools.shrink.jobchooser import JobChooser
from mediatools.shrink.jobregistry import JobRegistry
from mediatools.shrink.jobspec import JobSpec
from mediatools.shrink.ledger import Ledger
from mediatools.shrink.resulttable import ResultTable
from mediatools.shrink.savingslog import SavingsLog
from mediatools.shrink.spacetally import SpaceTally
from mediatools.snapshot import SnapshotSettings, Snapshotter, SystemdTrigger
from mediatools.sync import Syncer, TargetPicker
from mediatools.update import ReleaseUpdater
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
        self.argv: list[str] = []

    # -- parser ----------------------------------------------------------
    @staticmethod
    def _add_shrink_parsers(sub: argparse._SubParsersAction) -> None:
        """Define ``shrink`` and the commands around a running shrink.

        ``stop`` and ``attach`` act on a shrink run in the background;
        ``update`` installs a new release.

        Args:
            sub: Sub-parser collection of the main parser.
        """
        cmd = sub.add_parser("shrink", help="re-encode videos to HEVC (libx265) in place")
        cmd.add_argument("path", nargs="?", help="directory (default: [collection] root)")
        cmd.add_argument("--keep-copies", action="store_true",
                         help="do not replace originals; keep shrunk copies in newShrinkedFiles/")
        cmd.add_argument("--foreground", action="store_true",
                         help="stay in this terminal; by default shrink moves into the "
                              "background after the confirmation (see 'mediatools attach')")
        cmd.add_argument("--crf", type=int, default=28, help="libx265 CRF (default 28)")
        cmd.add_argument("--preset", choices=HevcEncoder.PRESETS, default=None,
                         help="libx265 preset: faster is quicker, slower compresses more "
                              "(default: [shrink] preset, else medium)")
        cmd.add_argument("--threads", type=int, default=None,
                         help="CPU threads for decoding and x265 (default: logical CPUs - 1)")
        cmd.add_argument("--ext", default="mp4", help="video extensions (default mp4)")
        cmd.add_argument("--order", choices=ShrinkOptions.ORDERS, default="largest",
                         help="processing order (default: largest files first)")
        cmd.add_argument("--min-seconds", type=float, default=0.0,
                         help="leave out videos shorter than this (e.g. 60 before a triage)")
        cmd.add_argument("--min-size", type=Cli._size_arg, default=None,
                         help="leave out videos smaller than this, e.g. 300M or 1.5G "
                              "(decimal units)")
        cmd.add_argument("--newer-than", type=Cli._date_arg, default=None, metavar="YYYY-MM-DD",
                         help="leave out videos last modified before this date")
        cmd.add_argument("--jobs", type=int, default=None,
                         help="videos encoded at the same time (default: [shrink] jobs, "
                              "else 1); try 2 and compare the MB/s")
        cmd.add_argument("--retry-failed", action="store_true",
                         help="include videos that failed in an earlier run (FAILED/BROKEN); "
                              "by default they are left out while their size is unchanged")
        cmd.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
        cmd.add_argument("-v", "--verbose", action="store_true", help="debug logging")
        cmd.add_argument("--continue-log", help=argparse.SUPPRESS)   # set by a release switch

        sub.add_parser("reload", help="ask a running shrink to switch to the newly installed "
                                      "release after its current file; returns at once")
        sub.add_parser("status", help="show whether a shrink runs, with which release, "
                                      "whether it is current, and its log")
        sub.add_parser("stop", help="ask a running shrink (in another terminal) to stop "
                                    "after its current file; returns at once")
        cmd = sub.add_parser("update", help="apply delivered release patches from the download "
                                            "folder (asks which version), then install")
        cmd.add_argument("--list", action="store_true", help="only show what would be applied")
        cmd.add_argument("-y", "--yes", action="store_true",
                         help="apply all pending versions without asking")
        cmd.add_argument("--downloads", help="folder with the patch archives (default ~/Downloads)")
        cmd.add_argument("--repo", help="git clone (default ~/Projects/mediatools)")

        sub.add_parser("attach", aliases=["front"],
                       help="watch a shrink running in the background (log and progress); "
                            "Ctrl+C leaves the view, the run continues")

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

        Cli._add_shrink_parsers(sub)

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

        cmd = sub.add_parser("where", help="show where configuration, logs, state and data "
                                           "are kept")
        cmd.add_argument("--migrate", action="store_true",
                         help="move the data of releases before 1.5.0 from "
                              "~/scripts/mediatools.d into the XDG folders")

        cmd = sub.add_parser("privacy", help="show or edit the local lists of the privacy scan")
        cmd.add_argument("--edit", choices=("deny", "allow"),
                         help="open the deny or allow list in $EDITOR (created if missing)")

        cmd = sub.add_parser("snapshot", help="versioned backups with rsnapshot")
        cmd.add_argument("action", choices=["check", "adopt", "init", "run", "status",
                                            "install-trigger"])
        cmd.add_argument("existing", nargs="?", help="adopt: existing mirror on the disk")
        cmd.add_argument("--no-stand0", action="store_true",
                         help="adopt: do not keep a permanent copy of the first state")
        cmd.add_argument("--dry-run", action="store_true", help="run: probe only")
        cmd.add_argument("-y", "--yes", action="store_true",
                         help="run: no questions; abort instead of asking")

        cmd = sub.add_parser("savings", help="space freed by shrinking per session or per "
                                             "day, from the shrink logs (read-only)")
        cmd.add_argument("--by", choices=("session", "day"), default="session",
                         help="one row per shrink session (default) or per calendar day")

        cmd = sub.add_parser("gui", help="graphical interface for all commands")
        group = cmd.add_mutually_exclusive_group()
        group.add_argument("--install-desktop", action="store_true",
                           help="add mediatools to the application menu and end")
        group.add_argument("--remove-desktop", action="store_true",
                           help="remove mediatools from the application menu and end")

    # -- dispatch --------------------------------------------------------
    @staticmethod
    def cmd_savings(args: argparse.Namespace) -> int:
        """Run ``mediatools savings``: freed space per session or per day.

        Args:
            args: Parsed arguments.

        Returns:
            0.
        """
        for line in SavingsLog(DataDir().sub("logs")).report(args.by):
            print(line)
        return 0

    def cmd_gui(self, args: argparse.Namespace) -> int:
        """Run ``mediatools gui``: open the window or manage its menu entry.

        Args:
            args: Parsed arguments.

        Returns:
            0 on success, 2 if no window can open.
        """
        return GuiLauncher(self.parser, DesktopEntry.default_program()).run(
            args.install_desktop, args.remove_desktop)

    def main(self, argv: list[str] | None = None) -> int:
        """Parse arguments and run the selected subcommand.

        Args:
            argv: Arguments without the program name; ``sys.argv`` if None.

        Returns:
            Process exit code.
        """
        self.argv = list(argv) if argv is not None else sys.argv[1:]
        args, extra = self.parser.parse_known_args(self.argv)
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
    def _size_arg(text: str) -> int:
        """Parse ``--min-size`` (argparse type).

        Args:
            text: Size such as ``300M``.

        Returns:
            Bytes.

        Raises:
            argparse.ArgumentTypeError: If the text is not a size.
        """
        try:
            return JobSpec.parse_size(text)
        except ValueError as err:
            raise argparse.ArgumentTypeError(str(err)) from None

    @staticmethod
    def _date_arg(text: str) -> float:
        """Parse ``--newer-than`` (argparse type).

        Args:
            text: Date ``YYYY-MM-DD``.

        Returns:
            Epoch seconds of local midnight.

        Raises:
            argparse.ArgumentTypeError: If the text is not a date.
        """
        try:
            return JobSpec.parse_date(text)
        except ValueError as err:
            raise argparse.ArgumentTypeError(str(err)) from None

    @staticmethod
    def ledger() -> Ledger:
        """Return the ledger of finished videos (filled in from the logs once).

        Returns:
            ``<data>/ledger.tsv``.
        """
        datadir = DataDir()
        ledger = Ledger(os.path.join(datadir.data_dir, "ledger.tsv"), datadir.sub("logs"))
        ledger.ensure()
        return ledger

    @staticmethod
    def registry(ledger: Ledger | None = None) -> JobRegistry:
        """Return the remembered shrink jobs.

        Args:
            ledger: Ledger for the figures done; None leaves them 0.

        Returns:
            ``<state>/jobs.json``.
        """
        return JobRegistry(os.path.join(DataDir().sub("state"), "jobs.json"), ledger)

    def _choose_job(self, args: argparse.Namespace) -> argparse.Namespace | None:
        """Offer the earlier jobs for a plain ``mediatools shrink`` on a terminal.

        Args:
            args: Parsed arguments.

        Returns:
            The arguments to run (those of the chosen job, or unchanged),
            or None if the user ended the selection.
        """
        if self.argv != ["shrink"] or not sys.stdin.isatty():
            return args
        chosen = JobChooser(self.registry(self.ledger())).choose()
        if chosen is None:
            return None
        if not chosen:
            return args
        self.argv = chosen                       # a reload continues this very job
        return self.parser.parse_args(chosen)

    @staticmethod
    def _preset(given: str | None, config: Config) -> str:
        """Choose the libx265 preset: option, else ``[shrink] preset``, else medium.

        Args:
            given: Value of ``--preset``, or None.
            config: Configuration file.

        Returns:
            The preset.

        Raises:
            SystemExit: If the configured preset is unknown.
        """
        preset = given or config.get("shrink", "preset") or "medium"
        if preset not in HevcEncoder.PRESETS:
            raise SystemExit(f"Error: [shrink] preset must be one of "
                             f"{', '.join(HevcEncoder.PRESETS)}, found {preset!r}")
        return preset

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
            Exit code of :meth:`Shrinker.run` (0 if the user declined), or 3
            if another shrink run is still active.
        """
        active = RunControl("shrink").active_pid()
        if active is not None and active != os.getpid():   # own PID: after a release switch
            print(f"shrink is already running (pid {active}) - watch it with "
                  f"'mediatools attach', stop it with 'mediatools stop'")
            return 3
        chosen = self._choose_job(args)
        if chosen is None:
            return 0
        args = chosen
        config = Config()
        root = self._dir(config.require_path("collection", "root", args.path))
        self._need("ffmpeg", "ffprobe")
        mode = "keep shrunk copies" if args.keep_copies else "REPLACE the originals"
        run = RunLog("shrink", args.verbose, continue_path=args.continue_log)
        probe = MediaProbe()
        encoder = HevcEncoder(args.crf, args.threads, probe, run.logger, decode=DecodeCheck(),
                              preset=self._preset(args.preset, config))
        durations = DurationCache(os.path.join(DataDir().sub("state"), "triage_durations.tsv"))
        jobs = args.jobs if args.jobs is not None else (config.integer("shrink", "jobs")
                                                        if config.get("shrink", "jobs") else 1)
        opts = ShrinkOptions(args.keep_copies, MediaTypes.parse_extensions(args.ext),
                             config.db_name(), args.order, args.min_seconds,
                             args.retry_failed, args.min_size or 0, args.newer_than, max(1, jobs))
        codecs = CodecCache(os.path.join(DataDir().sub("state"), "shrink_codecs.tsv"))
        shrinker = Shrinker(root, run.logger, opts, encoder, probe, durations, codecs=codecs)
        shrinker.control = RunControl(
            "shrink", background=not args.foreground, log_path=run.path,
            inhibitor=SleepInhibitor("mediatools shrink", run.logger))
        shrinker.state["results"] = ResultTable(
            root, DataDir().file("reports", "shrink_results", ".txt"), run.logger,
            shutil.get_terminal_size().columns - 1)
        ledger = self.ledger()
        job = JobSpec(root, opts.extensions, opts.min_seconds, opts.min_size, opts.newer_than)
        session = os.path.basename(run.path)[:-4]
        done = ledger.sums(job)
        shrinker.state.update({"ledger": ledger, "registry": self.registry(ledger), "job": job,
                               "session": session,
                               "space": SpaceTally.from_ledger(ledger, session),
                               "base": ProgressBase(done.read, done.files, done.since,
                                                    written=done.freed_out)})
        shrinker.state["decode"] = DecodeCheck()
        shrinker.state["ffmpeg_logs"] = os.path.join(DataDir().sub("logs"), "ffmpeg")
        restart = SelfRestart(self.argv, run.path)
        shrinker.state["restart"] = restart
        shrinker.install_signal_handlers()
        prompt = Prompt(args.yes)
        code = shrinker.run(lambda: prompt.yes_no(f"Start encoding and {mode}?"))
        if code == Shrinker.RESTART:
            return self._switch_release(restart, run)
        return code

    @staticmethod
    def _switch_release(restart: SelfRestart, run: RunLog) -> int:
        """Replace this process by the installed release (see :class:`SelfRestart`).

        Args:
            restart: Command line and log of the new process.
            run: Log of this run; closed before the switch.

        Returns:
            1 - only if the new process could not be started.
        """
        run.logger.info("starting the installed release: %s", " ".join(restart.command()))
        run.close()
        try:
            restart.execute()
        except OSError as err:
            print(f"switch to the installed release failed ({err}) - run "
                  f"'mediatools shrink' again to continue", file=sys.stderr)
        return 1

    @staticmethod
    def cmd_reload(_args: argparse.Namespace) -> int:
        """Run ``mediatools reload``: ask the running shrink to switch releases.

        Returns:
            0 if a running shrink was asked, 1 if none runs.
        """
        pid = RunControl("shrink").request_restart()
        if pid is None:
            print("no shrink run is active - nothing to reload")
            return 1
        print(f"reload requested: shrink (pid {pid}) switches to the installed release "
              f"after its current file")
        return 0

    @staticmethod
    def cmd_status(_args: argparse.Namespace) -> int:
        """Run ``mediatools status``: running shrink, its release and log.

        Returns:
            0 if a shrink runs, 1 if none runs.
        """
        control = RunControl("shrink")
        print("\n".join(RunStatus(control).lines()))
        return 0 if control.active_pid() is not None else 1

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

    @staticmethod
    def cmd_update(args: argparse.Namespace) -> int:
        """Run ``mediatools update``: apply delivered release patches.

        Args:
            args: Parsed arguments.

        Returns:
            Exit code of the bundled release script.
        """
        forwarded = ["--list"] if args.list else []
        forwarded += ["-y"] if args.yes else []
        forwarded += ["--downloads", args.downloads] if args.downloads else []
        forwarded += ["--repo", args.repo] if args.repo else []
        code = ReleaseUpdater(forwarded).run()
        if code == 0 and not args.list:
            entry = DesktopEntry(DesktopEntry.default_program())
            for item in entry.repair(Config().get("gui", "desktop_entry") == "yes"):
                print("application menu entry repaired (" + item + ")")
        return code

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

    @staticmethod
    def cmd_where(args: argparse.Namespace) -> int:
        """Run ``mediatools where``: list the folders, optionally move old data.

        Args:
            args: Parsed arguments.

        Returns:
            0 on success, 1 if the move is refused.
        """
        if args.migrate:
            if os.environ.get(DataDir.ENV):
                print(f"${DataDir.ENV} is set - the move applies only to the default layout")
                return 1
            if not DataDir.legacy_in_use():
                print(f"nothing to move: {DataDir.legacy_dir()} holds no data")
            else:
                try:
                    for old, new in DataMigration().run():
                        print(f"moved  {old}\n    -> {new}")
                except RuntimeError as error:
                    print(f"not moved: {error}")
                    return 1
            print()
        for line in PlacesReport().lines():
            print(line)
        entry = DesktopEntry(DesktopEntry.default_program())
        found = entry.problems()
        wanted = Config().get("gui", "desktop_entry") == "yes"
        print(f"application menu entry: {entry.path}: "
              + ("ok" if not found else "; ".join(found))
              + ("" if wanted or not found else
                 "  (create it with: mediatools gui --install-desktop)"))
        return 0

    @staticmethod
    def cmd_privacy(args: argparse.Namespace) -> int:
        """Run ``mediatools privacy``: show or edit the local lists.

        Args:
            args: Parsed arguments.

        Returns:
            0, or the exit code of the editor.
        """
        lists = PrivacyLists()
        if args.edit:
            return lists.edit(args.edit)
        for line in lists.lines():
            print(line)
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
