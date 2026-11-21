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
from mediatools.shrink.jobregistry import JobEntry, JobRegistry
from mediatools.shrink.jobspec import JobSpec
from mediatools.shrink.jobsuggester import JobSuggester, SuggestedJob
from mediatools.shrink.ledger import Ledger
from mediatools.shrink.options import ShrinkOptions
from mediatools.shrink.potential import ShrinkPotential
from mediatools.shrink.shrinker import Shrinker
from mediatools.shrink.shrinkqueue import ShrinkQueue
from mediatools.shrink.throughput import ThroughputLog
from mediatools.shrink.timemodel import TimeModel


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
        cmd.add_argument("--suggest", type=int, default=0, metavar="N",
                         help="propose up to N shrink jobs (level and --min-seconds), the "
                              "most space per hour first")
        cmd.add_argument("--queue", action="store_true",
                         help="with --suggest: put the proposed jobs into the shrink queue, "
                              "with the settings of the last shrink job")
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
                ``probe_jobs``, ``suggest``, ``queue``).

        Returns:
            0.
        """
        log = self.runlog.logger
        levels = self.levels(getattr(args, "up", 0) or 0, getattr(args, "up_to", None))
        data = DataDir()
        opts = ShrinkOptions(keep_copies=True, extensions=MediaTypes.parse_extensions(args.ext),
                             db_name=self.db_name, probe_jobs=max(1, args.probe_jobs),
                             probe_durations=True)
        shrinker = Shrinker(levels[-1], log, opts, probe=MediaProbe(),
                            durations=DurationCache(data.cache_file("triage_durations.tsv")),
                            codecs=CodecCache(data.cache_file("shrink_codecs.tsv")))
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
        if getattr(args, "suggest", 0) > 0:
            jobs = JobSuggester(potential).suggest(args.suggest)
            self.report_jobs(jobs, potential.model.uncertain)
            if getattr(args, "queue", False):
                self.enqueue(jobs, levels, frozenset(opts.extensions))
        elif getattr(args, "queue", False):
            log.warning("--queue works only with --suggest N - nothing queued")
        return 0

    def report_jobs(self, jobs: list[SuggestedJob], uncertain: bool) -> None:
        """Log the proposed jobs with running totals.

        Args:
            jobs: Proposed jobs, best first.
            uncertain: True if times are ranges (fixed part extrapolated).
        """
        log = self.runlog.logger
        gib = 1024 ** 3
        if not jobs:
            log.info("no shrink job proposed: no combination frees at least 1 GiB")
            return
        log.info("proposed shrink jobs, the most space per hour first%s:",
                 " (times: optimistic to cautious)" if uncertain else "")
        freed = hours = 0.0
        for number, job in enumerate(jobs, 1):
            freed += job.saved
            hours += job.seconds
            log.info("  %i. shrink L%i --min-seconds %-4i %6i files  %7.1f GiB freed in %s  "
                     "(%.1f GiB/h; total %.1f GiB in %s)", number, job.level, job.min_seconds,
                     job.files, job.saved / gib, self.time_text(job.seconds_low, job.seconds),
                     job.per_hour / gib, freed / gib, ThroughputLog.duration_text(hours))

    def enqueue(self, jobs: list[SuggestedJob], levels: list[str],
                extensions: frozenset) -> None:
        """Put the proposed jobs into the shrink queue, in their order.

        The settings (CRF, preset, parallel jobs, threads) are those of the
        most recently active shrink job, so the queued jobs run as the user
        usually runs them; options that change what is selected
        (``--retry-failed``, ``--keep-copies``) are not taken over.

        Args:
            jobs: Proposed jobs, best first.
            levels: Folders of the levels, innermost first.
            extensions: Extensions of the estimate.
        """
        data = DataDir()
        known = JobRegistry(os.path.join(data.sub("state"), "jobs.json"), None, data.sub("logs"))
        recent = known.entries()
        settings = {key: value for key, value in (recent[0].settings if recent else {}).items()
                    if key in ("crf", "preset", "jobs", "threads")}
        queue = ShrinkQueue.default()
        for job in jobs:
            spec = JobSpec(levels[job.level - 1], extensions, float(job.min_seconds))
            queue.add(JobEntry(spec, settings).argv()[1:],
                      f"estimate: L{job.level}, about {job.saved / 1024 ** 3:.0f} GiB")
        self.runlog.logger.info("%i job(s) added to the shrink queue (settings of the last "
                                "job: %s) - they start when the running shrink ends, or with "
                                "'mediatools queue --run'; 'mediatools queue' lists them",
                                len(jobs), settings or "defaults")

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
        model = potential.model
        for quality, ratio in sorted(potential.ratios.items()):
            log.info("  %-12s output %3.0f %%  %5.1f x real time  (%i measured)", quality,
                     ratio * 100, model.speed(quality), potential.samples[quality])
        log.info("fixed time per video: %s (%s)", self.fixed_text(model), model.basis)
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
            for label, files, size, saved, seconds, low in potential.thresholds(level):
                log.info("  %-20s %6i files %8.1f GiB -> about %7.1f GiB freed in %s", label,
                         files, size / gib, saved / gib, self.time_text(low, seconds))
        path = DataDir().file("reports", "shrink_estimate", ".tsv")
        self.write_report(path, potential, levels)
        log.info("report: %s", path)

    @staticmethod
    def fixed_text(model: TimeModel) -> str:
        """Describe the fixed time per video.

        Args:
            model: :class:`TimeModel`.

        Returns:
            ``6.0 s``, or ``0 to 52.4 s`` while it is only extrapolated.
        """
        if model.uncertain:
            return f"{model.fixed_low:.0f} to {model.fixed:.1f} s"
        return f"{model.fixed:.1f} s"

    @staticmethod
    def time_text(low: float, high: float) -> str:
        """Format an expected duration, as a range if the two variants differ.

        Args:
            low: Optimistic seconds.
            high: Cautious seconds.

        Returns:
            ``1 d 7 h`` or ``1 d 7 h to 1 d 22 h``.
        """
        cautious = ThroughputLog.duration_text(high)
        optimistic = ThroughputLog.duration_text(low)
        return cautious if optimistic == cautious else f"{optimistic} to {cautious}"

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
            handle.write(f"# fixed seconds per video\t{potential.model.fixed_low:.1f}\t"
                         f"{potential.model.fixed:.1f}\t{potential.model.basis}\n")
            handle.write("level\tlength\tbit_rate\tfiles\tbytes\tfreed_bytes\tseconds\t"
                         "seconds_low\tplaying_seconds\toutput_share\n")
            for group in potential.ranked():
                handle.write(f"{group.level}\t{group.band}\t{group.quality}\t{group.files}\t"
                             f"{group.size}\t{group.saved:.0f}\t{group.seconds:.0f}\t"
                             f"{group.seconds_low:.0f}\t{group.length:.0f}\t"
                             f"{group.ratio:.3f}\n")
