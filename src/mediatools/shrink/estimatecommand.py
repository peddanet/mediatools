"""The ``estimate`` command: how much shrinking could still free, and at what cost in time."""
from __future__ import annotations

import argparse
import os

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
    run while a shrink job is active. Videos recorded in a shrink database,
    recorded as failed, or already HEVC are left out exactly as ``shrink``
    does; the rest is grouped by playing time and bit rate
    (:class:`ShrinkPotential`) with the measured output share and speed.
    Durations and codecs come from the caches of shrink; missing ones are
    probed with several ffprobe processes at once and added to the caches.

    Attributes:
        root: Directory to estimate.
        runlog: Log of this command.
    """

    def __init__(self, root: str, db_name: str | None) -> None:
        """Bind the command to a directory.

        Args:
            root: Directory.
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
                                              "length and bit rate, with the time it takes "
                                              "(changes nothing)")
        cmd.add_argument("path", nargs="?", help="directory (default: [collection] root)")
        cmd.add_argument("--ext", default="mp4", help="video extensions (default mp4)")
        cmd.add_argument("--probe-jobs", type=int, default=8,
                         help="ffprobe processes at the same time for durations and codecs "
                              "not known yet (default 8)")

    def run(self, args: argparse.Namespace) -> int:
        """Plan, group and report.

        Args:
            args: Parsed arguments (``ext``, ``probe_jobs``).

        Returns:
            0.
        """
        log = self.runlog.logger
        state = DataDir().sub("state")
        opts = ShrinkOptions(keep_copies=True, extensions=MediaTypes.parse_extensions(args.ext),
                             db_name=self.db_name, probe_jobs=max(1, args.probe_jobs))
        shrinker = Shrinker(self.root, log, opts, probe=MediaProbe(),
                            durations=DurationCache(os.path.join(state, "triage_durations.tsv")),
                            codecs=CodecCache(os.path.join(state, "shrink_codecs.tsv")))
        log.info("estimate for %s (nothing is changed)", self.root)
        todo = shrinker.planner.plan(lambda: False)
        planner = shrinker.planner
        planner.prefetch("durations", [path for path, _size in todo])
        throughput = ThroughputLog()
        ledger = Ledger(os.path.join(DataDir().data_dir, "ledger.tsv"))
        potential = ShrinkPotential(ledger.rows() if os.path.exists(ledger.path) else [],
                                    throughput.rate())
        for path, size in todo:
            potential.add(size, planner.duration(path))
        if planner.state["durations"] is not None:
            planner.state["durations"].save()
        self.report(potential, shrinker, len(todo))
        return 0

    def report(self, potential: ShrinkPotential, shrinker: Shrinker, count: int) -> None:
        """Log the basis, the ranked groups and the thresholds, and write a TSV report.

        Args:
            potential: Grouped videos.
            shrinker: The planning run (for its counters).
            count: Videos still to shrink.
        """
        log = self.runlog.logger
        gib = 1024 ** 3
        stats = shrinker.stats
        log.info("left out: already processed (database) %i | failed before %i | already HEVC "
                 "%i", stats["skipped"], stats["failed_before"], len(shrinker.found["hevc"]))
        log.info("basis: output %.0f %% of the source and %.1f MB/s (measured on %i video(s)); "
                 "per bit rate where at least %i were measured:", potential.overall_ratio * 100,
                 potential.overall_rate / 1e6, sum(potential.samples.values()),
                 ShrinkPotential.MIN_SAMPLES)
        for quality, ratio in sorted(potential.ratios.items()):
            log.info("  %-12s output %3.0f %%  %5.1f MB/s  (%i measured)", quality, ratio * 100,
                     potential.rates[quality] / 1e6, potential.samples[quality])
        log.info("%i video(s) not shrunk yet - best groups first (most GiB freed per hour):",
                 count)
        for line in potential.table():
            log.info("  %s", line)
        log.info("what each --min-seconds would take in:")
        for label, files, size, saved, seconds in potential.thresholds():
            log.info("  %-20s %6i files %8.1f GiB -> about %7.1f GiB freed in %s", label, files,
                     size / gib, saved / gib, ThroughputLog.duration_text(seconds))
        path = DataDir().file("reports", "shrink_estimate", ".tsv")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("length\tbit_rate\tfiles\tbytes\tfreed_bytes\tseconds\toutput_share\n")
            for group in potential.ranked():
                handle.write(f"{group.band}\t{group.quality}\t{group.files}\t{group.size}\t"
                             f"{group.saved:.0f}\t{group.seconds:.0f}\t{group.ratio:.3f}\n")
        log.info("report: %s", path)
