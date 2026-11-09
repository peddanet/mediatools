"""Shrinker behaviour without ffmpeg: order, filters, stopping, recovery."""
import io
import logging
import os
import shutil
import signal
from collections.abc import Callable
from pathlib import Path

import pytest

from conftest import make_file, read
from mediatools.core.codeccache import CodecCache
from mediatools.core.durationcache import DurationCache
from mediatools.core.progressbar import ProgressBar
from mediatools.shrink import ShrinkDatabase, Shrinker, ShrinkOptions, ThroughputLog
from mediatools.shrink.planner import ShrinkPlanner

LOG = logging.getLogger("test")


class FakeProbe:
    """Probe double: every file is H.264; durations from a table."""

    def __init__(self, durations: dict[str, float] | None = None) -> None:
        """Store the durations.

        Args:
            durations: Duration per file name; unknown names have 100 s.
        """
        self.durations = durations or {}

    def video_codec(self, _path: str) -> str:
        """Report H.264 for every file (path ignored).

        Returns:
            Always ``h264``.
        """
        return "h264"

    def duration(self, path: str) -> float:
        """Return the tabulated duration.

        Args:
            path: Video file.

        Returns:
            Seconds.
        """
        return self.durations.get(os.path.basename(path), 100.0)


class FakeEncoder:
    """Encoder double that writes ``shrunk:<name>`` and can trigger signals."""

    def __init__(self, signals_at: dict[str, int] | None = None) -> None:
        """Configure the double.

        Args:
            signals_at: Number of SIGINTs to send while encoding a file name.
        """
        self.signals_at = signals_at or {}
        self.calls: list[str] = []
        self.shrinker: Shrinker | None = None
        self.aborted = False

    def encode(self, src: str, dst: str,
               on_progress: Callable[[float], None] | None = None) -> bool:
        """Pretend to encode.

        Args:
            src: Input file.
            dst: Output file.
            on_progress: Progress callback.

        Returns:
            False if aborted meanwhile, otherwise True.
        """
        name = os.path.basename(src)
        self.calls.append(name)
        self.aborted = False
        if on_progress:
            on_progress(0.5)
        for _ in range(self.signals_at.get(name, 0)):
            assert self.shrinker is not None
            self.shrinker.on_signal(signal.SIGINT)
        if self.aborted:
            return False
        Path(dst).write_bytes(b"shrunk:" + name.encode())
        return True

    def abort(self) -> None:
        """Remember the abort request."""
        self.aborted = True


def build(tmp_path: Path, opts: ShrinkOptions | None = None,
          encoder: FakeEncoder | None = None,
          probe: FakeProbe | None = None) -> tuple[Shrinker, FakeEncoder]:
    """Create a Shrinker wired to the doubles.

    Args:
        tmp_path: Temporary directory.
        opts: Run settings.
        encoder: Encoder double; a new one if None.
        probe: Probe double; a new one if None.

    Returns:
        The Shrinker and its encoder double.
    """
    encoder = encoder or FakeEncoder()
    shrinker = Shrinker(str(tmp_path / "c"), LOG, opts, encoder,  # type: ignore[arg-type]
                        probe or FakeProbe(),  # type: ignore[arg-type]
                        throughput=ThroughputLog(str(tmp_path / "speed.json")),
                        display=ProgressBar(1, 1, stream=io.StringIO()))
    encoder.shrinker = shrinker
    return shrinker, encoder


def videos(tmp_path: Path) -> dict[str, str]:
    """Create three videos of different size in two directories.

    Args:
        tmp_path: Temporary directory.

    Returns:
        Path per file name.
    """
    return {"small.mp4": make_file(tmp_path / "c/a/small.mp4", b"s" * 10),
            "large.mp4": make_file(tmp_path / "c/b/large.mp4", b"l" * 1000),
            "medium.mp4": make_file(tmp_path / "c/a/medium.mp4", b"m" * 100)}


def test_largest_first_and_database_precedence(tmp_path: Path) -> None:
    """Default order is by size; recorded files are never encoded again.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    paths = videos(tmp_path)
    make_file(tmp_path / "c/a/empty.mp4", b"")              # placeholder: ignored
    shrinker, encoder = build(tmp_path)
    assert shrinker.run() == 0
    assert encoder.calls == ["large.mp4", "medium.mp4", "small.mp4"]
    assert read(paths["large.mp4"]) == b"shrunk:large.mp4"
    assert ShrinkDatabase(str(tmp_path / "c/b")).is_done(paths["large.mp4"])
    assert not os.path.exists(tmp_path / "c/a/newShrinkedFiles")
    again, encoder2 = build(tmp_path)
    assert again.run() == 0 and not encoder2.calls and again.stats["skipped"] == 3


def test_smallest_first_and_min_seconds(tmp_path: Path) -> None:
    """Short videos are left out; the rest follows the chosen order.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    videos(tmp_path)
    probe = FakeProbe({"small.mp4": 20.0})
    shrinker, encoder = build(tmp_path, ShrinkOptions(order="smallest", min_seconds=60),
                              probe=probe)
    assert shrinker.run() == 0
    assert encoder.calls == ["medium.mp4", "large.mp4"] and shrinker.stats["short"] == 1


def test_first_ctrl_c_finishes_current_file(tmp_path: Path) -> None:
    """One Ctrl+C: the current file is completed and recorded, then stop.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    paths = videos(tmp_path)
    shrinker, encoder = build(tmp_path, encoder=FakeEncoder({"large.mp4": 1}))
    assert shrinker.run() == 1
    assert encoder.calls == ["large.mp4"]
    assert read(paths["large.mp4"]) == b"shrunk:large.mp4"
    assert read(paths["medium.mp4"]) == b"m" * 100
    resumed, encoder2 = build(tmp_path)
    assert resumed.run() == 0 and encoder2.calls == ["medium.mp4", "small.mp4"]


def test_second_ctrl_c_aborts_without_touching_original(tmp_path: Path) -> None:
    """Two Ctrl+C: the current encode is dropped, the original kept.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    paths = videos(tmp_path)
    shrinker, _encoder = build(tmp_path, encoder=FakeEncoder({"large.mp4": 2}))
    assert shrinker.run() == 1
    assert read(paths["large.mp4"]) == b"l" * 1000
    assert not ShrinkDatabase(str(tmp_path / "c/b")).is_done(paths["large.mp4"])
    assert not os.path.exists(tmp_path / "c/b/newShrinkedFiles")


def test_interrupted_replacement_is_completed(tmp_path: Path) -> None:
    """A marker next to a validated output repeats the copy on the next run.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    original = make_file(tmp_path / "c/d/v.mp4", b"half-written")
    make_file(tmp_path / "c/d/newShrinkedFiles/v.mp4", b"validated output")
    make_file(tmp_path / "c/d/newShrinkedFiles/v.mp4.replace", original.encode())
    make_file(tmp_path / "c/d/newShrinkedFiles/orphan.mp4.replace", b"")
    shrinker, encoder = build(tmp_path)
    assert shrinker.run() == 0
    assert read(original) == b"validated output" and shrinker.stats["recovered"] == 1
    assert ShrinkDatabase(str(tmp_path / "c/d")).is_done(original)
    assert not encoder.calls and not os.path.exists(tmp_path / "c/d/newShrinkedFiles")


def test_declined_confirmation_changes_nothing(tmp_path: Path) -> None:
    """Answering no after the plan summary encodes nothing.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    videos(tmp_path)
    shrinker, encoder = build(tmp_path)
    assert shrinker.run(lambda: False) == 0 and not encoder.calls


def test_options_validation() -> None:
    """Unknown order and negative durations are rejected."""
    for kwargs in ({"order": "random"}, {"min_seconds": -1}):
        with pytest.raises(ValueError):
            ShrinkOptions(**kwargs)  # type: ignore[arg-type]


def test_progress_bar_and_throughput(tmp_path: Path) -> None:
    """The status line shows percentage and an estimate from known speed.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    stream = io.StringIO()
    display = ProgressBar(4 * 1024 ** 3, 4, initial_rate=1024 ** 2, stream=stream)
    display.file_done(1024 ** 3)
    line = display.line("clip.mp4", 0.5, 1024 ** 3)
    assert "37.5 %" in line and "1/4 files" in line and "ETA 0d" not in line
    assert display.remaining_seconds() == 3 * 1024
    display.show("clip.mp4")                       # not a terminal: nothing drawn
    assert stream.getvalue() == ""
    assert ProgressBar.duration_text(3 * 86400 + 7200) == "3d02h"
    log = ThroughputLog(str(tmp_path / "t.json"))
    assert log.rate() is None
    log.add(100, 4.0)
    assert ThroughputLog(str(tmp_path / "t.json")).rate() == 25.0
    assert ThroughputLog.duration_text(3 * 3600 + 120) == "3 h 2 min"


def test_replacement_flushes_output_marker_and_directory_first(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Output, marker and directory reach the disk before the original is touched.

    Args:
        tmp_path: Temporary directory (fixture).
        monkeypatch: Patcher (fixture).
    """
    paths = videos(tmp_path)
    events: list[str] = []
    real_fsync, real_copy = os.fsync, shutil.copyfile

    def fsync(descriptor: int) -> None:
        """Record which file or directory is flushed.

        Args:
            descriptor: Open file descriptor.
        """
        events.append("fsync:" + os.path.basename(os.readlink(f"/proc/self/fd/{descriptor}")))
        real_fsync(descriptor)

    def copy(src: str, dst: str) -> str:
        """Record the copy over the original.

        Args:
            src: Source file.
            dst: Destination file.

        Returns:
            The destination.
        """
        events.append("copy:" + os.path.basename(dst))
        return real_copy(src, dst)

    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(shutil, "copyfile", copy)
    shrinker, _encoder = build(tmp_path, ShrinkOptions(order="path"))
    assert shrinker.run() == 0
    name = events[0].split(":", 1)[1]               # first file in walk order
    assert events[:5] == [f"fsync:{name}", f"fsync:{name}.replace", "fsync:newShrinkedFiles",
                          f"copy:{name}", f"fsync:{name}"]
    assert read(paths[name]) == b"shrunk:" + name.encode()


def test_sighup_aborts_the_current_file(tmp_path: Path) -> None:
    """A closed terminal (SIGHUP) aborts at once and keeps the original.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    paths = videos(tmp_path)
    shrinker, encoder = build(tmp_path)
    encoder.signals_at = {"large.mp4": 0}
    original_encode = encoder.encode

    def hangup(src: str, dst: str, on_progress: Callable[[float], None] | None = None) -> bool:
        """Send SIGHUP while the first file is encoded.

        Args:
            src: Input file.
            dst: Output file.
            on_progress: Progress callback.

        Returns:
            Result of the double.
        """
        shrinker.on_signal(signal.SIGHUP)
        return original_encode(src, dst, on_progress)

    encoder.encode = hangup  # type: ignore[method-assign]
    assert shrinker.run() == 1
    assert read(paths["large.mp4"]) == b"l" * 1000
    assert shrinker.display is not None and not shrinker.display.enabled


def test_leftovers_listed_and_removed_only_after_confirmation(tmp_path: Path) -> None:
    """Leftover work folders survive a declined start question.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    videos(tmp_path)
    leftover = make_file(tmp_path / "c/a/newShrinkedFiles/old_partial.mp4", b"x" * 50)
    shrinker, encoder = build(tmp_path)
    assert shrinker.run(lambda: False) == 0
    assert os.path.exists(leftover) and not encoder.calls
    assert shrinker.found["leftovers"][0][1:] == (1, 50)
    shrinker, encoder = build(tmp_path)
    assert shrinker.run(lambda: True) == 0
    assert not os.path.exists(leftover) and len(encoder.calls) == 3


def test_size_forecast_from_measured_ratio(tmp_path: Path,
                                           caplog: pytest.LogCaptureFixture) -> None:
    """The plan summary forecasts the size after shrinking.

    Args:
        tmp_path: Temporary directory (fixture).
        caplog: Log capture (fixture).
    """
    videos(tmp_path)
    log = ThroughputLog(str(tmp_path / "speed.json"))
    log.add(1000, 10.0)                       # speed only (format of 1.2.0)
    assert log.ratio() is None
    log.add(1000, 10.0, 400)
    assert ThroughputLog(str(tmp_path / "speed.json")).ratio() == 0.4
    shrinker, _encoder = build(tmp_path)
    with caplog.at_level(logging.INFO, logger="test"):
        shrinker.run(lambda: False)
    assert "expected afterwards: about" in caplog.text and "40 % of the source" in caplog.text


class HevcProbe(FakeProbe):
    """Probe double that reports HEVC for chosen file names."""

    def __init__(self, hevc: set[str]) -> None:
        """Store the HEVC names.

        Args:
            hevc: File names reported as HEVC.
        """
        super().__init__()
        self.hevc = hevc
        self.codec_calls = 0

    def video_codec(self, path: str) -> str:
        """Report HEVC for the chosen names, H.264 otherwise.

        Args:
            path: Video file.

        Returns:
            ``hevc`` or ``h264``.
        """
        self.codec_calls += 1
        return "hevc" if os.path.basename(path) in self.hevc else "h264"


def test_hevc_set_aside_in_plan_and_recorded_after_confirmation(
        tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Already-HEVC videos are reported apart, never encoded, recorded only after yes.

    Args:
        tmp_path: Temporary directory (fixture).
        caplog: Log capture (fixture).
    """
    paths = videos(tmp_path)
    probe = HevcProbe({"large.mp4"})
    shrinker, encoder = build(tmp_path, probe=probe)
    with caplog.at_level(logging.INFO, logger="test"):
        assert shrinker.run(lambda: False) == 0
    assert "already HEVC, recorded without encoding: 1 files" in caplog.text
    assert "to encode: 2 files" in caplog.text
    assert not ShrinkDatabase(str(tmp_path / "c/b")).is_done(paths["large.mp4"])
    shrinker, encoder = build(tmp_path, probe=HevcProbe({"large.mp4"}))
    shrinker.display = None                          # let the run size the display
    assert shrinker.run(lambda: True) == 0
    assert "large.mp4" not in encoder.calls and shrinker.stats["hevc"] == 1
    assert ShrinkDatabase(str(tmp_path / "c/b")).is_done(paths["large.mp4"])
    assert shrinker.display is not None and shrinker.display.total_files == 2
    assert shrinker.display.total_bytes == 110


def test_codec_cache_answers_repeated_plans(tmp_path: Path) -> None:
    """A second plan takes the codecs from the cache.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    videos(tmp_path)
    cache_file = str(tmp_path / "codecs.tsv")
    probe = HevcProbe(set())
    shrinker, _encoder = build(tmp_path, probe=probe)
    shrinker.state["codecs"] = CodecCache(cache_file)
    shrinker.plan()
    first = probe.codec_calls
    probe2 = HevcProbe(set())
    shrinker, _encoder = build(tmp_path, probe=probe2)
    shrinker.state["codecs"] = CodecCache(cache_file)
    shrinker.plan()
    assert first == 3 and probe2.codec_calls == 0


def test_plan_saves_probes_at_checkpoints_and_logs_cache_hits(
        tmp_path: Path, caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Probes are stored during planning, not only at its end; hits are logged.

    Args:
        tmp_path: Temporary directory (fixture).
        caplog: Log capture (fixture).
        monkeypatch: Patch helper (fixture).
    """
    videos(tmp_path)
    cache_file = tmp_path / "durations.tsv"
    shrinker, _encoder = build(tmp_path, ShrinkOptions(min_seconds=1))
    monkeypatch.setattr(ShrinkPlanner, "CHECKPOINT", 2)
    durations = DurationCache(str(cache_file))
    shrinker.state["durations"] = durations
    saved: list[int] = []
    real_save = durations.save

    def counting_save() -> None:
        saved.append(len(durations.entries))
        real_save()

    durations.save = counting_save                           # type: ignore[method-assign]
    shrinker.plan()
    assert saved == [1, 3]                 # at file 2 (before probing it) and at the end
    shrinker, _encoder = build(tmp_path, ShrinkOptions(min_seconds=1))
    shrinker.state["durations"] = DurationCache(str(cache_file))
    caplog.set_level(logging.INFO, logger="test")
    shrinker.plan()
    assert "checking durations: 3 of 3 (3 from cache)" in caplog.text


def test_file_hevc_by_now_is_removed_from_progress_totals(tmp_path: Path) -> None:
    """A file that became HEVC during the run (hard link) leaves the totals.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    videos(tmp_path)

    class LateHevc(FakeProbe):
        """H.264 while planning, HEVC when the file is processed."""

        def __init__(self) -> None:
            """Start in planning mode."""
            super().__init__()
            self.calls = 0

        def video_codec(self, path: str) -> str:
            """Report H.264 for the three plan probes, then HEVC for small.mp4.

            Args:
                path: Video file.

            Returns:
                Codec name.
            """
            self.calls += 1
            if self.calls > 3 and os.path.basename(path) == "small.mp4":
                return "hevc"
            return "h264"

    shrinker, encoder = build(tmp_path, probe=LateHevc())
    shrinker.display = None                          # let the run size the display
    assert shrinker.run() == 0
    assert "small.mp4" not in encoder.calls and shrinker.stats["hevc"] == 1
    assert shrinker.display is not None
    assert shrinker.display.total_files == 2 and shrinker.display.done_files == 2
