"""Release 1.11.0: estimate over several levels, fixed time per video, one probe per video."""
import argparse
import os
import subprocess  # nosec B404 - test helper runs ffmpeg with a fixed list
from pathlib import Path

import pytest

from conftest import make_file, make_video
from test_release_190 import MB, row
from test_shrink_run import FakeProbe, build, videos
from mediatools.cli import Cli
from mediatools.core.codeccache import CodecCache
from mediatools.core.datadir import DataDir
from mediatools.core.durationcache import DurationCache
from mediatools.core.probe import MediaProbe
from mediatools.shrink.estimatecommand import EstimateCommand
from mediatools.shrink.ledger import Ledger
from mediatools.shrink.options import ShrinkOptions
from mediatools.shrink.potential import ShrinkPotential
from mediatools.shrink.throughput import ThroughputLog


class BothProbe(FakeProbe):
    """Probe double that answers duration and codec with one counted call."""

    def __init__(self) -> None:
        """Start counting."""
        super().__init__()
        self.calls = 0

    def duration_and_codec(self, path: str) -> tuple[float | None, str | None]:
        """Count and answer.

        Args:
            path: Video.

        Returns:
            ``(seconds, codec)``; a file named ``none.mp4`` has neither.
        """
        self.calls += 1
        if os.path.basename(path) == "none.mp4":
            return None, None
        return self.duration(path), self.video_codec(path)


# -- one ffprobe call for both values ------------------------------------------------
def test_probe_reads_duration_and_codec_at_once(tmp_path: Path) -> None:
    """A real video gives both values; an unreadable file gives neither.

    Args:
        tmp_path: Temporary directory.
    """
    video = make_video(tmp_path / "v.mp4", seconds=2)
    seconds, codec = MediaProbe().duration_and_codec(video)
    assert seconds == pytest.approx(2.0, abs=0.2) and codec == "h264"
    assert MediaProbe().duration_and_codec(make_file(tmp_path / "x.mp4", b"no")) == (None, None)
    audio = tmp_path / "a.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=duration=1",
                    "-c:a", "aac", str(audio)], check=True)
    seconds, codec = MediaProbe().duration_and_codec(str(audio))
    assert seconds is not None and codec is None


def test_prefetch_both_fills_both_caches(tmp_path: Path) -> None:
    """Missing values are probed once per video; cached ones are not probed again.

    Args:
        tmp_path: Temporary directory.
    """
    paths = list(videos(tmp_path).values())
    probe = BothProbe()
    shrinker, _encoder = build(tmp_path, ShrinkOptions(probe_jobs=2), probe=probe)
    durations = DurationCache(str(tmp_path / "d.tsv"))
    codecs = CodecCache(str(tmp_path / "c.tsv"))
    shrinker.state["durations"], shrinker.state["codecs"] = durations, codecs
    none = make_file(tmp_path / "c" / "none.mp4", b"n")
    shrinker.planner.prefetch_both(paths + [none, str(tmp_path / "gone.mp4")])
    assert probe.calls == 4
    assert len(DurationCache(str(tmp_path / "d.tsv")).entries) == 3
    assert len(CodecCache(str(tmp_path / "c.tsv")).entries) == 3
    shrinker.planner.prefetch_both(paths)
    assert probe.calls == 4 and durations.hits == 0


def test_prefetch_both_falls_back(tmp_path: Path) -> None:
    """Without a combined probe or without a codec cache only durations are probed.

    Args:
        tmp_path: Temporary directory.
    """
    paths = list(videos(tmp_path).values())
    shrinker, _encoder = build(tmp_path, ShrinkOptions(probe_jobs=2))
    shrinker.state["durations"] = DurationCache(str(tmp_path / "d.tsv"))
    shrinker.state["codecs"] = CodecCache(str(tmp_path / "c.tsv"))
    shrinker.planner.prefetch_both(paths)
    assert len(DurationCache(str(tmp_path / "d.tsv")).entries) == 3
    assert not os.path.exists(tmp_path / "c.tsv")


def test_plan_probes_both_when_asked(tmp_path: Path) -> None:
    """``probe_durations`` makes planning fill the duration cache as well.

    Args:
        tmp_path: Temporary directory.
    """
    videos(tmp_path)
    probe = BothProbe()
    opts = ShrinkOptions(probe_jobs=2, probe_durations=True, keep_copies=True)
    shrinker, _encoder = build(tmp_path, opts, probe=probe)
    shrinker.state["durations"] = DurationCache(str(tmp_path / "d.tsv"))
    shrinker.state["codecs"] = CodecCache(str(tmp_path / "c.tsv"))
    assert len(shrinker.planner.plan(lambda: False)) == 3
    assert probe.calls == 3 and len(shrinker.state["durations"].entries) == 3


# -- fixed time per video --------------------------------------------------------------
def test_fit_and_fixed_time() -> None:
    """The time is fitted as a + b x size; a goes per video, the rest scales with size."""
    rows = [row(10 * MB, 1 * MB, 10.0, 60.0), row(30 * MB, 3 * MB, 30.0, 600.0)] * 10
    assert ShrinkPotential.fit(rows) == pytest.approx((0.0, 1e-6))
    shifted = [row(r.source, r.result or 0, r.took + 5.0, r.length or 0) for r in rows]
    assert ShrinkPotential.fit(shifted) == pytest.approx((5.0, 1e-6))
    assert ShrinkPotential.fit(rows[:1]) is None
    assert ShrinkPotential.fit([rows[0], rows[0]]) is None
    potential = ShrinkPotential(shifted, wall_rate=None)
    assert potential.overhead == pytest.approx(5.0)
    assert "fitted on 20 measured video(s), 10 of them shorter than 5 min" \
        in potential.overhead_basis
    potential.add(10 * MB, 30.0)
    group = potential.ranked()[0]
    assert group.seconds == pytest.approx(5.0 + 10.0, rel=0.05)
    assert ShrinkPotential.variable(10.0, 3, 5.0) == 1.0
    few = ShrinkPotential(shifted[:3], wall_rate=None)
    assert few.overhead == 0.0 and "not measurable" in few.overhead_basis
    given = ShrinkPotential(shifted, wall_rate=None, overhead=-1.0)
    assert given.overhead == 0.0 and given.overhead_basis == "set with --overhead"


def test_levels_in_groups_and_thresholds() -> None:
    """Groups and thresholds are kept per level."""
    potential = ShrinkPotential([], 2 * MB, 0.0)
    potential.add(600 * MB, 700.0, "L1")
    potential.add(10 * MB, 30.0, "L2")
    potential.add(10 * MB, 30.0, "L10")
    assert potential.levels() == ["L1", "L2", "L10"]
    first = dict((label, files) for label, files, *_rest in potential.thresholds("L1"))
    second = dict((label, files) for label, files, *_rest in potential.thresholds("L2"))
    assert first["--min-seconds 600"] == 1 and first["--min-seconds 0"] == 1
    assert second["--min-seconds 60"] == 0 and second["--min-seconds 0"] == 1
    assert len(potential.table(1)) == 2 and potential.table()[1].startswith("L")


# -- levels of the command -------------------------------------------------------------
def test_level_folders_and_errors(tmp_path: Path) -> None:
    """--up and --up-to add parents; impossible requests stop with a message.

    Args:
        tmp_path: Temporary directory.
    """
    inner = tmp_path / "a" / "b"
    os.makedirs(inner)
    command = EstimateCommand(str(inner), None)
    assert command.levels(0, None) == [str(inner)]
    assert command.levels(2, None) == [str(inner), str(tmp_path / "a"), str(tmp_path)]
    assert command.levels(0, str(tmp_path)) == [str(inner), str(tmp_path / "a"), str(tmp_path)]
    assert command.levels(0, str(inner)) == [str(inner)]
    with pytest.raises(SystemExit):
        command.levels(-1, None)
    with pytest.raises(SystemExit):
        command.levels(0, str(tmp_path / "other"))
    with pytest.raises(SystemExit):
        EstimateCommand("/", None).levels(1, None)
    assert 2 <= EstimateCommand.default_probe_jobs() <= 16


def test_level_of_and_hard_links(tmp_path: Path) -> None:
    """A video counts for its innermost level, a hard-linked video only once.

    Args:
        tmp_path: Temporary directory.
    """
    inner = tmp_path / "outer" / "in"
    levels = [str(inner), str(tmp_path / "outer")]
    one = make_file(inner / "x" / "one.mp4", b"1")
    other = str(tmp_path / "outer" / "y" / "one.mp4")
    os.makedirs(os.path.dirname(other))
    os.link(one, other)
    two = make_file(tmp_path / "outer" / "y" / "two.mp4", b"2")
    assert EstimateCommand.level_of(one, levels) == "L1"
    assert EstimateCommand.level_of(two, levels) == "L2"
    assert EstimateCommand.level_of("/elsewhere/z.mp4", levels) == "L2"
    kept, dropped = EstimateCommand.unique([(other, 1), (two, 1), (one, 1),
                                            (str(tmp_path / "gone.mp4"), 1)], levels)
    assert dropped == 2 and kept == [(one, 1), (two, 1)]


def test_estimate_over_two_levels(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """One run reports L1 and what L2 adds; paths go only into the report.

    Args:
        tmp_path: Temporary directory.
        capsys: Output capture.
    """
    outer = tmp_path / "outer"
    make_video(outer / "in" / "a" / "one.mp4", seconds=3)
    make_video(outer / "b" / "two.mp4", seconds=2)
    ledger = Ledger(os.path.join(DataDir().data_dir, "ledger.tsv"))
    ledger.add(row(30 * MB, 3 * MB, 10.0, 60.0))
    ThroughputLog().add(30 * MB, 10.0, 3 * MB)
    assert Cli().main(["estimate", str(outer / "in"), "--up", "1", "--overhead", "4"]) == 0
    out = capsys.readouterr().out
    assert "L1 and 1 level(s) above" in out and str(outer) not in out
    assert "L1 only" in out and "L2 only" in out
    assert "fixed time per video: 4.0 s (set with --overhead)" in out
    assert "2 video(s) not shrunk yet" in out
    reports = sorted(os.listdir(DataDir().sub("reports")))
    text = Path(DataDir().sub("reports"), reports[-1]).read_text(encoding="utf-8")
    assert f"# L2\t{outer}" in text and "\nL1\t" in text and "\nL2\t" in text


def test_report_limits_the_ranked_rows(tmp_path: Path, capsys: pytest.CaptureFixture[str],
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """More groups than REPORT_ROWS: the console shows the first ones and says so.

    Args:
        tmp_path: Temporary directory.
        capsys: Output capture.
        monkeypatch: Patcher.
    """
    make_video(tmp_path / "c" / "one.mp4", seconds=2)
    make_video(tmp_path / "c" / "two.mp4", seconds=2, codec="libx264")
    monkeypatch.setattr(EstimateCommand, "REPORT_ROWS", 0)
    args = argparse.Namespace(up=0, up_to=None, overhead=None, ext="mp4", probe_jobs=1)
    assert EstimateCommand(str(tmp_path / "c"), None).run(args) == 0
    out = capsys.readouterr().out
    assert "first 0" in out and "only" not in out
