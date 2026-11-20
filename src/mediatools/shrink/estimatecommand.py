"""The ``estimate`` command: how much shrinking could still free, and at what cost in time."""
from __future__ import annotations

import argparse
import os
import time

from mediatools.core.codeccache import CodecCache
from mediatools.core.datadir import DataDir
from mediatools.core.durationcache import DurationCache
from mediatools.core.mediatypes import MediaTypes
from mediatools.core.probe import MediaProbe
from mediatools.core.runlog import RunLog
from mediatools.shrink.ledger import Ledger
from mediatools.shrink.options import ShrinkOptions
from mediatools.shrink.potential import ShrinkPotential
from mediatools.shrink.shrinker import Shrinker
from mediatools.shrink.throughput import ThroughputLog


class EstimateCommand:
    """Plans like ``shrink`` without encoding and estimates the potential.

    Read only: no database, cache of a run or video is changed, interrupted
    replacements are not completed (that is left to ``shrink``), and it can
    run while a shrink job is active. It runs only when it is called; no
    other command starts it. Videos recorded in a shrink database, recorded
    as failed, or already HEVC are left out exactly as ``shrink`` does; the
    rest is grouped by level, playing time and bit rate
    (:class:`ShrinkPotential`) with the measured output share, speed and
    fixed time per video.

    **Levels.** ``L1`` is the given folder (default ``[collection] root``);
    with ``--up N`` or ``--up-to DIR`` its parents are added as ``L2``,
    ``L3`` ... Each level counts only what it adds to the level inside it,
    so the rows answer "what does going one level up bring". All levels
    are planned in **one** walk of the outermost folder, and every video is
    counted once even if it has several hard-linked names.

    **Speed of the estimate itself.** Durations and codecs come from the
    caches of shrink; a video missing there is probed once for both values
    (one ffprobe call), ``--probe-jobs`` at a time. The console names the
    levels only by label; their paths are in the report file.

    Attributes:
        root: The given folder (``L1``).
        db_name: File name of the shrink databases.
        runlog: Log of this command.
    """

    REPORT_ROWS = 20

    def __init__(self, root: str, db_name: str | None) -> None:
        """Bind the command to a directory.

        Args:
            root: Directory (``L1``).
            db_name: File name of the shrink databases.
        """
        self.root = os.path.abspath(root)
        self.db_name = db_name
        self.runlog = RunLog("estimate")

    @staticmethod
    def add_parser(sub: argparse._SubParsersAction) -> None:
        """Define the subcommand.

        Args:
            sub: Sub-parser collection of the main parser.
        """
        cmd = sub.add_parser("estimate", help="how much shrinking could still free, grouped by "
                                              "level, length and bit rate, with the time it "
                                              "takes (changes nothing)")
        cmd.add_argument("path", nargs="?", help="directory, level L1 (default: [collection] "
                                                 "root)")
        levels = cmd.add_mutually_exclusive_group()
        levels.add_argument("--up", type=int, default=0, metavar="N",
                            help="also estimate N parent folders (L2, L3 ...), each with "
                                 "only what it adds")
        levels.add_argument("--up-to", metavar="DIR",
                            help="also estimate every parent folder up to DIR, e.g. ~")
        cmd.add_argument("--overhead", type=float, default=None, metavar="SECONDS",
                         help="fixed seconds per video instead of the value fitted from the "
                              "ledger")
        cmd.add_argument("--ext", default="mp4", help="video extensions (default mp4)")
        cmd.add_argument("--probe-jobs", type=int, default=EstimateCommand.default_probe_jobs(),
                         help="ffprobe processes at the same time for durations and codecs "
                              "not known yet (default: CPU threads, at most 16)")

    @staticmethod
    def default_probe_jobs() -> int:
        """Return the default number of parallel ffprobe processes.

        Returns:
            The number of CPU threads, at least 2 and at most 16.
        """
        return max(2, min(16, os.cpu_count() or 2))

    def levels(self, up: int, up_to: str | None) -> list[str]:
        """Return the folders of the levels, the given one first.

        Args:
            up: Number of parent folders to add.
            up_to: Add every parent up to this folder, or None.

        Returns:
            Absolute folders ``L1``, ``L2`` ... (each contains the previous).

        Raises:
            SystemExit: If ``up`` is negative, goes above ``/`` or ``up_to``
                does not contain the given folder.
        """
        found = [self.root]
        if up_to:
            outer = os.path.abspath(os.path.expanduser(up_to))
            if os.path.commonpath([outer, self.root]) != outer:
                raise SystemExit(f"Error: --up-to must be a parent of the folder ({outer})")
            while found[-1] != outer:
                found.append(os.path.dirname(found[-1]))
            return found
        if up < 0:
            raise SystemExit("Error: --up must not be negative")
        for _ in range(up):
            parent = os.path.dirname(found[-1])
            if parent == found[-1]:
                raise SystemExit("Error: --up goes above the root of the file system")
            found.append(parent)
        return found

    @staticmethod
    def level_of(path: str, levels: list[str]) -> str:
        """Return the label of the innermost level that contains a video.

        Args:
            path: Absolute video path.
            levels: Folders, innermost first.

        Returns:
            ``L1``, ``L2`` ...
        """
        for number, folder in enumerate(levels, 1):
            if os.path.commonpath([folder, path]) == folder:
                return f"L{number}"
        return f"L{len(levels)}"

    @classmethod
    def unique(cls, todo: list[tuple[str, int]],
               levels: list[str]) -> tuple[list[tuple[str, int]], int]:
        """Keep one name per video file (inode); hard links are encoded once.

        The name in the innermost level is kept, so a video linked into
        ``L1`` and ``L3`` counts for ``L1``.

        Args:
            todo: ``(path, size)`` pairs.
            levels: Folders of the levels, innermost first.

        Returns:
            The pairs without further names of the same inode, and the
            number of names dropped.
        """
        seen: set[tuple[int, int]] = set()
        kept = []

        def order(item: tuple[str, int]) -> tuple[int, str]:
            """Sort key: level number, then path.

            Args:
                item: ``(path, size)``.

            Returns:
                ``(level number, path)``.
            """
            return int(cls.level_of(item[0], levels)[1:]), item[0]

        for path, size in sorted(todo, key=order):
            try:
                st = os.stat(path)
            except OSError:
                continue
            key = (st.st_dev, st.st_ino)
            if key in seen:
                continue
            seen.add(key)
            kept.append((path, size))
        return kept, len(todo) - len(kept)

    def run(self, args: argparse.Namespace) -> int:
        """Plan once over the outermost level, group by level and report.

        Args:
            args: Parsed arguments (``up``, ``up_to``, ``overhead``, ``ext``,
                ``probe_jobs``).

        Returns:
            0.
        """
        log = self.runlog.logger
        levels = self.levels(getattr(args, "up", 0) or 0, getattr(args, "up_to", None))
        state = DataDir().sub("state")
        opts = ShrinkOptions(keep_copies=True, extensions=MediaTypes.parse_extensions(args.ext),
                             db_name=self.db_name, probe_jobs=max(1, args.probe_jobs),
                             probe_durations=True)
        shrinker = Shrinker(levels[-1], log, opts, probe=MediaProbe(),
                            durations=DurationCache(os.path.join(state, "triage_durations.tsv")),
                            codecs=CodecCache(os.path.join(state, "shrink_codecs.tsv")))
        log.info("estimate for L1%s (nothing is changed; the paths are in the report)",
                 f" and {len(levels) - 1} level(s) above" if len(levels) > 1 else "")
        started = time.monotonic()
        todo, dropped = self.unique(shrinker.planner.plan(lambda: False), levels)
        planner = shrinker.planner
        took = time.monotonic() - started
        log.info("planned in %s (one walk, ffprobe %i at a time)",
                 ThroughputLog.duration_text(took) if took >= 60 else f"{took:.0f} s",
                 opts.probe_jobs)
        ledger = Ledger(os.path.join(DataDir().data_dir, "ledger.tsv"))
        potential = ShrinkPotential(ledger.rows() if os.path.exists(ledger.path) else [],
                                    ThroughputLog().rate(), getattr(args, "overhead", None))
        for path, size in todo:
            potential.add(size, planner.duration(path), self.level_of(path, levels))
        if planner.state["durations"] is not None:
            planner.state["durations"].save()
        self.report(potential, shrinker, (len(todo), dropped), levels)
        return 0

    def report(self, potential: ShrinkPotential, shrinker: Shrinker,
               counts: tuple[int, int], levels: list[str]) -> None:
        """Log the basis, the ranked groups and the thresholds per level; write a TSV report.

        Args:
            potential: Grouped videos.
            shrinker: The planning run (for its counters).
            counts: Videos still to shrink, further hard-link names dropped.
            levels: Folders of the levels, innermost first.
        """
        log = self.runlog.logger
        gib = 1024 ** 3
        stats = shrinker.stats
        log.info("left out: already processed (database) %i | failed before %i | already HEVC "
                 "%i | further hard-link names %i", stats["skipped"], stats["failed_before"],
                 len(shrinker.found["hevc"]), counts[1])
        log.info("basis: output %.0f %% of the source and %.1f MB/s (measured on %i video(s)); "
                 "per bit rate where at least %i were measured:", potential.overall_ratio * 100,
                 potential.overall_rate / 1e6, sum(potential.samples.values()),
                 ShrinkPotential.MIN_SAMPLES)
        for quality, ratio in sorted(potential.ratios.items()):
            log.info("  %-12s output %3.0f %%  %5.1f MB/s  (%i measured)", quality, ratio * 100,
                     potential.rates[quality] / 1e6, potential.samples[quality])
        log.info("fixed time per video: %.1f s (%s)", potential.overhead,
                 potential.overhead_basis)
        if len(levels) > 1:
            log.info("levels: L1 = the given folder, L2 = its parent ... - each row counts only "
                     "what a level adds to the level inside it")
        log.info("%i video(s) not shrunk yet - best groups first (most GiB freed per hour)%s:",
                 counts[0], f", first {self.REPORT_ROWS}"
                 if len(potential.groups) > self.REPORT_ROWS else "")
        for line in potential.table(self.REPORT_ROWS):
            log.info("  %s", line)
        for level in potential.levels():
            log.info("what each --min-seconds would take in%s:",
                     f" - {level} only" if len(levels) > 1 else "")
            for label, files, size, saved, seconds in potential.thresholds(level):
                log.info("  %-20s %6i files %8.1f GiB -> about %7.1f GiB freed in %s", label,
                         files, size / gib, saved / gib, ThroughputLog.duration_text(seconds))
        path = DataDir().file("reports", "shrink_estimate", ".tsv")
        self.write_report(path, potential, levels)
        log.info("report: %s", path)

    @staticmethod
    def write_report(path: str, potential: ShrinkPotential, levels: list[str]) -> None:
        """Write all groups as TSV, the level folders as comment lines first.

        Args:
            path: Report file.
            potential: Grouped videos.
            levels: Folders of the levels, innermost first.
        """
        with open(path, "w", encoding="utf-8") as handle:
            for number, folder in enumerate(levels, 1):
                handle.write(f"# L{number}\t{folder}\n")
            handle.write(f"# fixed seconds per video\t{potential.overhead:.1f}\n")
            handle.write("level\tlength\tbit_rate\tfiles\tbytes\tfreed_bytes\tseconds\t"
                         "output_share\n")
            for group in potential.ranked():
                handle.write(f"{group.level}\t{group.band}\t{group.quality}\t{group.files}\t"
                             f"{group.size}\t{group.saved:.0f}\t{group.seconds:.0f}\t"
                             f"{group.ratio:.3f}\n")
