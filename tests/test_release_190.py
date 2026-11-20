"""Release 1.9.0: forecast line, estimate of the remaining potential, parallel probing."""
import io
import os
from pathlib import Path

import pytest

from conftest import make_file, make_video
from test_shrink_run import FakeProbe, build, videos
from mediatools.cli import Cli
from mediatools.core.codeccache import CodecCache
from mediatools.core.datadir import DataDir
from mediatools.core.durationcache import DurationCache
from mediatools.core.progressbar import ProgressBar
from mediatools.shrink.ledger import Ledger
from mediatools.shrink.ledgerrow import LedgerRow
from mediatools.shrink.options import ShrinkOptions
from mediatools.shrink.potential import PotentialGroup, ShrinkPotential
from mediatools.shrink.throughput import ThroughputLog

MB = 1_000_000


def row(source: int, result: int, took: float, length: float, status: str = "shrunk") -> LedgerRow:
    """Build a ledger entry.

    Args:
        source: Size before.
        result: Size after.
        took: Seconds spent.
        length: Playing time.
        status: Result status.

    Returns:
        The entry.
    """
    return LedgerRow("2026-11-19 10:00:00", "s", status, source, result, took, length, 0.0, "/v")


# -- forecast ------------------------------------------------------------------------
def test_forecast_line_uses_the_measured_share() -> None:
    """To go x (1 - share) is still to be freed; with the freed total the end figure."""
    gib = 1024 ** 3
    display = ProgressBar(100 * gib, 10, stream=io.StringIO())
    assert display.forecast_line() == ""
    display.base.ratio = 0.2
    assert display.forecast_line() == ("expect 100.0 GiB to go at 20 % output (measured) -> "
                                       "about 80.0 GiB more freed")
    display.base.freed = lambda: 1000 * gib
    display.done_bytes = 50 * gib
    assert display.forecast_line().endswith("about 40.0 GiB more freed, about 1040.0 GiB "
                                            "freed in total")
    status = io.StringIO()
    display.status.path = None
    display.stream = status
    display.show_many([("a.mp4", 0.5, gib)], "space  session: 1 GiB")
    assert display.drawn == 0                                   # not a terminal: nothing drawn


def test_shrink_run_sets_the_forecast(tmp_path: Path) -> None:
    """A run hands the measured share and the freed total to the display.

    Args:
        tmp_path: Temporary directory.
    """
    videos(tmp_path)
    speed = ThroughputLog(str(tmp_path / "speed.json"))
    speed.add(100, 1.0, 20)
    shrinker, _encoder = build(tmp_path, ShrinkOptions())
    shrinker.state["throughput"] = speed
    assert shrinker.run() == 0
    assert shrinker.display is not None and shrinker.display.base.ratio == pytest.approx(0.2)
    assert shrinker.display.base.freed is not None and shrinker.display.base.freed() >= 0


# -- potential ----------------------------------------------------------------------
def test_potential_ratio_and_speed_per_bit_rate() -> None:
    """Classes with enough measurements use their own share and speed, others the overall."""
    rows = [row(30 * MB, 3 * MB, 10.0, 60.0) for _ in range(ShrinkPotential.MIN_SAMPLES)]
    rows += [row(3 * MB, 1 * MB, 5.0, 60.0), row(1, 0, 0, 0), row(5, None, 1, 1, "FAILED")]
    potential = ShrinkPotential(rows, 6 * MB, 0.0)         # type: ignore[arg-type]
    fast = ShrinkPotential.quality_of(30 * MB, 60.0)
    assert fast == "4-8 Mbit/s" and potential.ratios[fast] == pytest.approx(0.1)
    slow = ShrinkPotential.quality_of(3 * MB, 60.0)
    assert potential.ratios[slow] == pytest.approx(potential.overall_ratio)
    assert potential.rates[slow] == 6 * MB and potential.samples[slow] == 1
    file_rate = (20 * 30 + 3) * MB / (20 * 10 + 5)
    assert potential.rates[fast] == pytest.approx(3 * MB * 6 * MB / file_rate)
    empty = ShrinkPotential([], None)
    assert empty.overall_ratio == ShrinkPotential.DEFAULT_RATIO
    assert empty.overall_rate == ShrinkPotential.DEFAULT_RATE


def test_potential_groups_rank_and_thresholds() -> None:
    """Groups by length and bit rate, best GiB per hour first, sums per --min-seconds."""
    potential = ShrinkPotential([], 2 * MB)
    potential.add(600 * MB, 700.0)          # 10-30 min, >= 6 Mbit/s
    potential.add(600 * MB, 700.0)
    potential.add(10 * MB, 30.0)            # < 1 min
    potential.add(5 * MB, None)             # unknown length
    assert ShrinkPotential.band_of(None) == ShrinkPotential.UNKNOWN
    assert ShrinkPotential.quality_of(1, 0) == ShrinkPotential.UNKNOWN
    ranked = potential.ranked()
    assert [group.files for group in ranked if group.band == "10-30 min"] == [2]
    lines = potential.table()
    assert lines[0].startswith("level length") and len(lines) == 4
    thresholds = dict((label, files) for label, files, _s, _f, _t in potential.thresholds())
    assert thresholds["--min-seconds 600"] == 2 and thresholds["--min-seconds 0"] == 3
    assert thresholds["--min-seconds 1800"] == 0
    group = PotentialGroup("x", "y", 1, 3600 * MB, ratio=0.25, rate=MB)
    assert group.saved == 2700 * MB and group.seconds == 3600
    assert group.per_hour == pytest.approx(2700 * MB)


# -- parallel probing ------------------------------------------------------------------
class CountingProbe(FakeProbe):
    """Probe double that counts duration probes."""

    def __init__(self) -> None:
        """Start counting."""
        super().__init__({"gone.mp4": 0.0})
        self.calls = 0

    def duration(self, path: str) -> float:
        """Count and answer.

        Args:
            path: Video.

        Returns:
            Seconds.
        """
        self.calls += 1
        return super().duration(path)


def test_prefetch_probes_missing_values_in_parallel(tmp_path: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing durations are probed once, stored and saved; cached ones are not probed.

    Args:
        tmp_path: Temporary directory.
        monkeypatch: Patcher.
    """
    paths = videos(tmp_path)
    probe = CountingProbe()
    shrinker, _encoder = build(tmp_path, ShrinkOptions(min_seconds=1, probe_jobs=4),
                               probe=probe)
    cache = DurationCache(str(tmp_path / "d.tsv"))
    shrinker.state["durations"] = cache
    monkeypatch.setattr(type(shrinker.planner), "CHECKPOINT", 2)
    shrinker.planner.prefetch("durations", list(paths.values()) + [str(tmp_path / "none.mp4")])
    assert probe.calls == 3 and len(DurationCache(str(tmp_path / "d.tsv")).entries) == 3
    shrinker.planner.prefetch("durations", list(paths.values()))
    assert probe.calls == 3                                   # all cached now
    assert cache.hits == 0                                    # prefetch does not count hits
    shrinker.state["durations"] = None
    shrinker.planner.prefetch("durations", list(paths.values()))
    assert probe.calls == 3
    codecs = CodecCache(str(tmp_path / "c.tsv"))
    shrinker.state["codecs"] = codecs
    shrinker.planner.prefetch("codecs", [paths["small.mp4"]])
    assert codecs.entries
    assert shrinker.planner.duration(paths["small.mp4"]) == 100.0
    with pytest.raises(ValueError):
        ShrinkOptions(probe_jobs=0)


# -- the estimate command --------------------------------------------------------------
def test_estimate_command_reports_without_changing_anything(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Planned like shrink, grouped, ranked; no database is created, a report is written.

    Args:
        tmp_path: Temporary directory.
        capsys: Output capture.
    """
    root = tmp_path / "coll"
    make_video(root / "a" / "one.mp4", seconds=3)
    make_video(root / "a" / "hevc.mp4", seconds=2, codec="libx265")
    make_file(root / "b" / "note.txt", b"x")
    ledger = Ledger(os.path.join(DataDir().data_dir, "ledger.tsv"))
    ledger.add(row(30 * MB, 3 * MB, 10.0, 60.0))
    ThroughputLog().add(30 * MB, 10.0, 3 * MB)
    assert Cli().main(["estimate", str(root), "--probe-jobs", "2"]) == 0
    out = capsys.readouterr().out
    assert "nothing is changed" in out and "already HEVC 1" in out
    assert "1 video(s) not shrunk yet" in out and "--min-seconds 0" in out
    assert "basis: output 10 %" in out
    assert not (root / "a" / "shrink.db").exists()
    reports = os.listdir(DataDir().sub("reports"))
    assert any(name.startswith("shrink_estimate_") for name in reports)
