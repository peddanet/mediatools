"""Release 1.7.0: setting damaged videos aside, measuring, reviewing, repairing."""
import io
import json
import os
import sqlite3
import stat
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from conftest import make_file, needs_ffmpeg
from mediatools.broken import (Assessment, Assessor, BackupMirror, BackupResult,
                               BrokenCollector, BrokenFinder,
                               BrokenReview, DownloaderDatabase, Quarantine, RepairEncoder)
from mediatools.cli import Cli
from mediatools.core.config import Config
from mediatools.core.decodecheck import DecodeCheck
from mediatools.core.mediatypes import MediaTypes
from mediatools.core.prompt import Prompt
from mediatools.core.runlog import RunLog
from mediatools.core.walker import TreeWalker
from mediatools.shrink.database import ShrinkDatabase
from mediatools.triage.remover import FileRemover


# -- helpers -----------------------------------------------------------------
def video(path: Path, source: str = "testsrc", seconds: int = 10) -> str:
    """Create a small H.264 video with its index at the start.

    Args:
        path: Output file.
        source: lavfi source, e.g. ``testsrc`` or ``color=c=gray``.
        seconds: Duration.

    Returns:
        The path as string.
    """
    os.makedirs(path.parent, exist_ok=True)
    spec = f"{source}{':' if '=' in source else '='}duration={seconds}:size=160x120:rate=10"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", spec, "-c:v", "libx264", "-movflags", "+faststart", str(path)],
                   check=True)
    return str(path)


def cut(path: str, share: float) -> str:
    """Cut a file to a share of its size (like an interrupted download).

    Args:
        path: File to shorten in place.
        share: Share of the bytes to keep.

    Returns:
        The path.
    """
    data = Path(path).read_bytes()
    Path(path).write_bytes(data[:int(len(data) * share)])
    return path


def logger(tmp_path: Path):
    """Return a run logger writing into the test's data directory.

    Args:
        tmp_path: Temporary directory (unused, keeps the call explicit).

    Returns:
        logging.Logger: The run logger.
    """
    del tmp_path
    return RunLog("broken-test").logger


def answers(*texts: str) -> Prompt:
    """Return a prompt that answers from a list.

    Args:
        *texts: Answers in order.

    Returns:
        The prompt.
    """
    queue = list(texts)
    return Prompt(input_func=lambda _question: queue.pop(0))


class StubPlayer:
    """Player double: remembers what it was asked to show.

    Attributes:
        shown: Lists of paths per :meth:`open` call.
        closed: Number of :meth:`close_all` calls.
    """

    def __init__(self) -> None:
        """Start without history."""
        self.shown: list[list[str]] = []
        self.closed = 0

    def open(self, paths: list[str]) -> None:
        """Record the paths.

        Args:
            paths: Videos to show.
        """
        self.shown.append(list(paths))

    def close_all(self) -> None:
        """Count the call."""
        self.closed += 1


class FixedProbe:
    """Probe double: every file lasts 5 s."""

    @staticmethod
    def duration(_path: str) -> float:
        """Report 5 s.

        Returns:
            5.0.
        """
        return 5.0


class StubRepairer(RepairEncoder):
    """Repair double: copies a prepared file as result or fails.

    Attributes:
        result: File copied to the output, or None for a failed repair.
    """

    def __init__(self, result: str | None) -> None:
        """Remember the result file.

        Args:
            result: File to deliver, or None.
        """
        super().__init__()
        self.result = result
        self.probe = FixedProbe()                    # type: ignore[assignment]

    def encode(self, src: str, dst: str,
               on_progress: Callable[[float], None] | None = None) -> bool:
        """Deliver the prepared result.

        Args:
            src: Damaged video.
            dst: Output.
            on_progress: Progress callback.

        Returns:
            True if a result was prepared.
        """
        if on_progress is not None:
            on_progress(0.5)
        if self.result is None:
            make_file(Path(dst + ".ffmpeg.log"))
            return False
        Path(dst).write_bytes(Path(self.result).read_bytes())
        return True


def failure(directory: str, name: str, status: str = "BROKEN", reason: str = "x") -> None:
    """Record a failure in the shrink database of a directory.

    Args:
        directory: Directory of the video.
        name: File name.
        status: ``BROKEN`` or ``FAILED``.
        reason: Reason text.
    """
    database = ShrinkDatabase(directory)
    database.ensure()
    database.record_failure(os.path.join(directory, name), status, reason)


@pytest.fixture(name="root")
def fixture_root(tmp_path: Path) -> Path:
    """Build a shrink root with an intact, a cut-off and a grey video.

    The cut-off video has a second name in ``_flat``; the grey one is
    recorded as BROKEN, the cut-off one is only listed in a report.

    Args:
        tmp_path: Temporary directory.

    Returns:
        The root.
    """
    root = tmp_path / "root"
    clips = root / "unit" / "Clips"
    video(clips / "ok.mp4")
    cut(video(clips / "cut.mp4"), 0.7)
    video(clips / "grey.mp4", "color=c=gray")
    os.makedirs(root / "unit" / "_flat")
    os.link(clips / "cut.mp4", root / "unit" / "_flat" / "Clips_cut.mp4")
    failure(str(clips), "grey.mp4")
    report = tmp_path / "report.tsv"
    report.write_text("check\tpath\n"
                      f"E  decode-ERROR\t{clips / 'cut.mp4'}\n"
                      f"E  decode-ok\t{clips / 'ok.mp4'}\n"
                      f"E  decode-ERROR\t{tmp_path / 'elsewhere.mp4'}\n", encoding="utf-8")
    return root


# -- assessment ----------------------------------------------------------------
def test_assessment_classes_and_texts() -> None:
    """Thresholds decide the class; texts and the manifest round trip work."""
    assert Assessment(False, note="bad").kind == "hopeless"
    assert "does not open (bad)" in Assessment(False, note="bad").summary()
    assert Assessment(True, 100, 5).kind == "hopeless"                 # < 10 % decodes
    assert Assessment(True, 100, 50, 46).kind == "hopeless"            # > 90 % still
    good = Assessment(True, 100, 80, 10, 3)
    assert good.kind == "promising" and good.repair_advised
    assert good.usable_share == pytest.approx(0.7)
    assert "1:40 min, 80 % decodes, 70 % usable, 3 decoder error(s)" == good.summary()
    assert not Assessment(True, 100, 40).repair_advised
    assert Assessment(True, 0, 5).decoded_share == 0.0
    assert Assessment(True, 0, 5).usable_share == 0.0
    assert Assessment.from_dict({**good.to_dict(), "extra": 1}) == good


def test_assessor_parsing() -> None:
    """Progress, still intervals (merged, open at the end) and messages are read."""
    assert Assessor.decoded_seconds("out_time_us=N/A\nout_time_us=2500000\nx=1\n") == 2.5
    assert Assessor.decoded_seconds("") == 0.0
    messages = ("[freezedetect @ 0x1] [info] lavfi.freezedetect.freeze_start: 1\n"
                "[freezedetect @ 0x1] [info] lavfi.freezedetect.freeze_end: 4\n"
                "[blackdetect @ 0x2] [info] black_start:3 black_end:6 black_duration:3\n"
                "[freezedetect @ 0x1] [info] lavfi.freezedetect.freeze_start: 8\n")
    assert Assessor.still_seconds(messages, 10.0) == pytest.approx(7.0)   # 1-6 and 8-10
    assert Assessor.merged_length([(2, 3), (0, 5), (4, 20)], 10) == 10
    assert Assessor.clean("[h264 @ 0x1] [error] Invalid data") == "Invalid data"


@needs_ffmpeg
def test_assessor_measures_real_videos(tmp_path: Path) -> None:
    """Intact, grey, cut-off and unreadable videos are classified as expected.

    Args:
        tmp_path: Temporary directory.
    """
    assessor = Assessor()
    ok = assessor.assess(video(tmp_path / "ok.mp4"))
    assert ok.kind == "promising" and ok.decoded_share > 0.95 and ok.errors == 0
    grey = assessor.assess(video(tmp_path / "grey.mp4", "color=c=gray"))
    assert grey.kind == "hopeless" and grey.opened
    broken = assessor.assess(cut(video(tmp_path / "cut.mp4"), 0.6))
    assert broken.kind == "promising" and broken.errors > 0 and broken.note
    assert 0.3 < broken.decoded_share < 0.9
    tiny = assessor.assess(make_file(tmp_path / "tiny.mp4", b"\0" * 64))
    assert not tiny.opened and tiny.kind == "hopeless"
    assert "freezedetect" in " ".join(assessor.command("x"))


def test_assessor_timeout_and_failed_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """A run that times out or fails without decoding is reported, not raised.

    Args:
        monkeypatch: Patcher.
    """
    class Probe:
        """Probe double with a video stream of 100 s."""

        @staticmethod
        def has_video(_path: str) -> bool:
            """Report a video stream.

            Returns:
                True.
            """
            return True

        @staticmethod
        def duration(_path: str) -> float:
            """Report 100 s.

            Returns:
                100.0.
            """
            return 100.0

    assessor = Assessor(probe=Probe())                      # type: ignore[arg-type]

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("ffmpeg", 1)

    monkeypatch.setattr(subprocess, "run", timeout)
    assert "did not finish" in assessor.assess("x").note
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a, 1, "", "[in#0 @ 0x1] [error] Error opening input\n"))
    failed = assessor.assess("x")
    assert not failed.opened and failed.note == "Error opening input"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a, 1, "", ""))
    assert assessor.assess("x").note == "ffmpeg failed"


# -- quarantine ----------------------------------------------------------------
def test_walker_and_database_helpers(tmp_path: Path) -> None:
    """``_broken`` is never walked; a failure reason can be read back.

    Args:
        tmp_path: Temporary directory.
    """
    make_file(tmp_path / "_broken" / "a.mp4")
    make_file(tmp_path / "b.mp4")
    assert [os.path.basename(p) for p in TreeWalker().files(str(tmp_path))] == ["b.mp4"]
    assert MediaTypes.BROKEN_DIR in MediaTypes.SKIP_DIRS
    failure(str(tmp_path), "b.mp4", reason="why")
    database = ShrinkDatabase(str(tmp_path))
    assert database.failure_reason("b.mp4") == "why"
    assert database.failure_reason("none.mp4") is None


def test_collect_moves_every_name_and_restores(tmp_path: Path) -> None:
    """All hard links move together, the manifest is private, restore records the keep.

    Args:
        tmp_path: Temporary directory.
    """
    root = tmp_path / "root"
    original = make_file(root / "u" / "a.mp4", b"data")
    os.makedirs(root / "u" / "_flat")
    os.link(original, root / "u" / "_flat" / "u_a.mp4")
    os.link(original, tmp_path / "outside.mp4")
    make_file(root / "u" / "single.mp4")
    log = logger(tmp_path)
    quarantine = Quarantine(str(root), log)
    item = quarantine.collect(original, Assessment(True, 10, 8), "test")
    assert item.kind == "promising" and item.outside == 1 and len(item.names) == 2
    assert item.held == str(root / "_broken" / "aussichtsreich" / "u" / "a.mp4")
    assert os.path.exists(root / "_broken" / "aussichtsreich" / "u" / "_flat" / "u_a.mp4")
    assert not os.path.exists(original) and quarantine.holds(original)
    assert stat.S_IMODE(os.stat(quarantine.manifest).st_mode) == 0o600
    record = json.loads(Path(quarantine.manifest).read_text(encoding="utf-8"))
    assert record["kind"] == "promising" and record["assessment"]["decoded"] == 8
    with pytest.raises(ValueError):
        quarantine.collect(item.held, Assessment(True), "again")
    with pytest.raises(ValueError):
        quarantine.collect(str(tmp_path / "outside.mp4"), Assessment(True), "outside")
    single = quarantine.collect(str(root / "u" / "single.mp4"), Assessment(False), "x")
    assert single.kind == "hopeless" and single.names[0][1].endswith("hoffnungslos/u/single.mp4")

    reloaded = Quarantine(str(root), log)
    assert [entry.number for entry in reloaded.open_items()] == [1, 2]
    first = reloaded.open_items("promising")[0]
    assert reloaded.restore(first)
    assert os.stat(original).st_nlink == 3 and first.state == "restored"
    assert ShrinkDatabase(str(root / "u")).failure_reason("a.mp4") == Quarantine.KEPT_REASON
    assert not reloaded.holds(original)


def test_target_names_and_restore_conflict(tmp_path: Path) -> None:
    """A taken name gets a number; a taken original blocks the restore.

    Args:
        tmp_path: Temporary directory.
    """
    root = tmp_path / "root"
    quarantine = Quarantine(str(root), logger(tmp_path))
    make_file(root / "_broken" / "aussichtsreich" / "u" / "a.mp4")
    assert quarantine.target(str(root / "u" / "a.mp4"), "promising").endswith("u/a.1.mp4")
    item = quarantine.collect(make_file(root / "u" / "a.mp4"), Assessment(True, 1, 1), "x")
    make_file(root / "u" / "a.mp4", b"new")
    assert not quarantine.restore(item) and item.state == "open"


def test_delete_and_install(tmp_path: Path) -> None:
    """Delete clears the failure; install puts the repair under every name.

    Args:
        tmp_path: Temporary directory.
    """
    root = tmp_path / "root"
    log = logger(tmp_path)
    quarantine = Quarantine(str(root), log)
    gone = make_file(root / "u" / "gone.mp4")
    failure(str(root / "u"), "gone.mp4")
    item = quarantine.collect(gone, Assessment(False), "x")
    assert quarantine.delete(item, FileRemover(True, log)) and item.state == "deleted"
    assert ShrinkDatabase(str(root / "u")).failures() == {}

    damaged = make_file(root / "u" / "d.mp4", b"damaged")
    os.makedirs(root / "u" / "_flat")
    os.link(damaged, root / "u" / "_flat" / "u_d.mp4")
    item = quarantine.collect(damaged, Assessment(True, 10, 9), "x")
    repaired = make_file(root / "_broken" / "work.mp4", b"repaired")
    assert quarantine.install(item, repaired, FileRemover(True, log))
    assert Path(damaged).read_bytes() == b"repaired" and os.stat(damaged).st_nlink == 2
    assert ShrinkDatabase(str(root / "u")).status(damaged) == "done"
    assert not os.path.exists(root / "u" / "_flat" / "shrink.db")
    assert not os.path.exists(item.held)

    other = quarantine.collect(make_file(root / "u" / "e.mp4"), Assessment(True, 1, 1), "x")
    make_file(root / "u" / "e.mp4", b"taken")
    assert not quarantine.install(other, repaired, FileRemover(True, log))
    failing = FileRemover(True, log)
    third = quarantine.collect(make_file(root / "u" / "f.mp4"), Assessment(False), "x")
    os.remove(third.held)
    assert not quarantine.delete(third, failing) and third.state == "open"


# -- finding and collecting ------------------------------------------------------
def test_finder_sources(tmp_path: Path) -> None:
    """BROKEN entries and report rows are found; FAILED, kept and changed are not.

    Args:
        tmp_path: Temporary directory.
    """
    unit = tmp_path / "root" / "u"
    for name in ("broken.mp4", "failed.mp4", "kept.mp4", "changed.mp4", "listed.mp4"):
        make_file(unit / name)
    failure(str(unit), "broken.mp4")
    failure(str(unit), "failed.mp4", "FAILED")
    failure(str(unit), "kept.mp4", reason=Quarantine.KEPT_REASON)
    failure(str(unit), "changed.mp4")
    make_file(unit / "changed.mp4", b"other size")
    make_file(unit / "missing.mp4")
    failure(str(unit), "missing.mp4")
    os.remove(unit / "missing.mp4")
    report = tmp_path / "list.tsv"
    report.write_text(f"path\n{unit / 'listed.mp4'}\n{tmp_path / 'root/_broken/x.mp4'}\n",
                      encoding="utf-8")
    found = BrokenFinder(str(tmp_path / "root"), report=str(report)).find()
    assert [os.path.basename(path) for path, _reason in found] == ["broken.mp4", "listed.mp4"]
    assert found[1][1] == "report: listed"
    bad = tmp_path / "bad.tsv"
    bad.write_text("name\nx\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        BrokenFinder(str(tmp_path / "root"), report=str(bad)).find()


@needs_ffmpeg
def test_collector_plan_and_run(root: Path, tmp_path: Path) -> None:
    """Measured, listed, asked; work-folder videos and held ones are left out.

    Args:
        root: Shrink root fixture.
        tmp_path: Temporary directory.
    """
    log = logger(tmp_path)
    clips = root / "unit" / "Clips"
    make_file(root / "unit" / "busy.mp4")
    failure(str(root / "unit"), "busy.mp4")
    make_file(root / "unit" / MediaTypes.SHRINK_TMP_DIR / "busy.mp4")
    finder = BrokenFinder(str(root), report=str(tmp_path / "report.tsv"))
    quarantine = Quarantine(str(root), log)
    collector = BrokenCollector(quarantine, finder, Assessor(), prompt=answers("n"))
    planned = collector.plan()
    assert [(os.path.basename(p), a.kind) for p, _r, a in planned] == \
        [("cut.mp4", "promising"), ("grey.mp4", "hopeless")]
    assert collector.run(dry_run=True) == 0 and os.path.exists(clips / "cut.mp4")
    assert collector.run() == 0 and os.path.exists(clips / "cut.mp4")       # answered no
    assert collector.run(ask=False) == 0
    assert not os.path.exists(clips / "cut.mp4") and len(quarantine.open_items()) == 2
    assert os.path.exists(root / "unit" / "busy.mp4")
    empty = BrokenCollector(quarantine, BrokenFinder(str(root)), Assessor())
    assert not empty.plan() and empty.run() == 0


# -- downloader database ---------------------------------------------------------
def test_downloader_database(tmp_path: Path) -> None:
    """The unique row is reset; ambiguous, missing and broken cases change nothing.

    Args:
        tmp_path: Temporary directory.
    """
    log = logger(tmp_path)
    root = tmp_path / "root"
    unit = root / "unit"
    meta = unit / "Metadata" / "media.db"
    os.makedirs(meta.parent)
    conn = sqlite3.connect(meta)
    conn.execute("CREATE TABLE medias (directory text, filename text, downloaded integer)")
    conn.executemany("INSERT INTO medias VALUES (?, ?, 1)",
                     [("/dl/unit/Clips/Part1", "a.mp4"), ("/dl/unit/Clips/Part1", "b.mp4"),
                      ("/dl/unit/Extra", "b.mp4"), ("/dl/unit/Clips/Part1", "c.mp4"),
                      ("/dl/unit/Other/Part1", "c.mp4")])
    conn.commit()
    conn.close()
    handler = DownloaderDatabase("Metadata/media.db", log)
    video_a = str(unit / "Clips" / "Part1" / "a.mp4")
    assert handler.reset(video_a, str(root)) == "marked for a new download"
    assert handler.reset(str(unit / "Clips" / "Part1" / "b.mp4"), str(root)) == \
        "marked for a new download"
    assert "2 matching rows" in handler.reset(str(unit / "c.mp4"), str(root))
    assert "0 matching rows" in handler.reset(str(unit / "x.mp4"), str(root))
    check = sqlite3.connect(meta)
    try:
        rows = check.execute("SELECT directory, filename, downloaded FROM medias "
                             "ORDER BY rowid").fetchall()
    finally:
        check.close()
    assert [row[2] for row in rows] == [0, 0, 1, 1, 1]
    assert "not configured" in DownloaderDatabase(" ", log).reset(video_a, str(root))
    assert DownloaderDatabase("none.db", log).reset(video_a, str(root)) == \
        "no downloader database found"
    make_file(unit / "plain.db", b"not a database at all, just bytes")
    assert "not changed" in DownloaderDatabase("plain.db", log).reset(video_a, str(root))


# -- repair ------------------------------------------------------------------------
@needs_ffmpeg
def test_repair_encoder(tmp_path: Path) -> None:
    """Tolerant input options; a cut-off video becomes a clean HEVC file.

    Args:
        tmp_path: Temporary directory.
    """
    encoder = RepairEncoder(decode=DecodeCheck(), preset="ultrafast", niceness=0)
    cmd = encoder.command("in.mp4", "out.mp4")
    assert cmd.index("-err_detect") < cmd.index("-i") and "+discardcorrupt" in cmd
    assert isinstance(encoder.clone(), RepairEncoder)
    assert RepairEncoder.output_for("/a/b/c.mkv") == "/a/b/repaired-c.mkv"
    damaged = cut(video(tmp_path / "cut.mp4"), 0.7)
    out = str(tmp_path / "out.mp4")
    assert encoder.encode(damaged, out)
    assert encoder.probe.video_codec(out) == "hevc"
    assert DecodeCheck().verify(out, 60) is None
    assert not encoder.validate(damaged, make_file(tmp_path / "empty.mp4", b"\0" * 32))
    encoder.decode = None
    assert encoder.validate(damaged, out)


@needs_ffmpeg
def test_repair_rejects_output_with_decode_errors(tmp_path: Path) -> None:
    """An output that does not decode cleanly is refused.

    Args:
        tmp_path: Temporary directory.
    """
    encoder = RepairEncoder(decode=DecodeCheck())
    damaged = cut(video(tmp_path / "cut.mp4"), 0.7)
    assert not encoder.validate("src.mp4", damaged)


# -- review --------------------------------------------------------------------------
def build_review(root: Path, tmp_path: Path, prompt: Prompt,
                 result: str | None = None) -> tuple[BrokenReview, Quarantine, StubPlayer]:
    """Set three plain files aside and wire a review with doubles.

    Args:
        root: Shrink root.
        tmp_path: Temporary directory.
        prompt: Scripted prompt.
        result: Repair result file, or None for a failing repair.

    Returns:
        ``(review, quarantine, player)``.
    """
    log = logger(tmp_path)
    quarantine = Quarantine(str(root), log)
    quarantine.collect(make_file(root / "u" / "good.mp4", b"g"), Assessment(True, 10, 9), "x")
    quarantine.collect(make_file(root / "u" / "half.mp4", b"h"), Assessment(True, 10, 4), "x")
    quarantine.collect(make_file(root / "u" / "lost.mp4", b"l"), Assessment(False), "x")
    for item in quarantine.items:              # checked: no copy in the backup
        quarantine.note_backup(item, BackupResult("missing"))
    player = StubPlayer()
    review = BrokenReview(quarantine, player, FileRemover(True, log),  # type: ignore[arg-type]
                          StubRepairer(result), DownloaderDatabase("", log),
                          BackupMirror(None, str(tmp_path), log), prompt=prompt)
    return review, quarantine, player


def test_review_promising_first_with_repair_keep_delete(tmp_path: Path) -> None:
    """Most usable first; repair accepted, keep, then the hopeless group deleted.

    Args:
        tmp_path: Temporary directory.
    """
    root = tmp_path / "root"
    result = make_file(tmp_path / "result.mp4", b"repaired")
    prompt = answers("", "p", "", "?", "a",          # good: advice r, play, wrong, accept
                     "x", "p", "", "k",              # half: wrong key, play, keep
                     "v", "", "a", "y")              # hopeless: view, delete all
    review, quarantine, player = build_review(root, tmp_path, prompt, result)
    assert review.run() == 0
    assert Path(root / "u" / "good.mp4").read_bytes() == b"repaired"
    assert Path(root / "u" / "half.mp4").read_bytes() == b"h"
    assert not os.path.exists(root / "u" / "lost.mp4")
    assert [item.state for item in quarantine.items] == ["repaired", "restored", "deleted"]
    assert len(player.shown) == 3 and player.closed == 3


def test_review_repair_discard_failure_skip_and_quit(tmp_path: Path) -> None:
    """Discarded and failed repairs leave the video aside; skip and quit end early.

    Args:
        tmp_path: Temporary directory.
    """
    root = tmp_path / "root"
    result = make_file(tmp_path / "result.mp4", b"repaired")
    review, quarantine, _player = build_review(root, tmp_path,
                                               answers("r", "x", "s", "q"), result)
    assert review.run() == 0
    assert [item.state for item in quarantine.items] == ["open", "open", "open"]
    assert not os.path.exists(RepairEncoder.output_for(quarantine.items[0].held))

    review, quarantine, _player = build_review(tmp_path / "root2", tmp_path,
                                               answers("r", "d", "n", "s", "s", "s"))
    assert review.run() == 0
    assert all(item.state == "open" for item in quarantine.items)
    assert not os.path.exists(RepairEncoder.output_for(quarantine.items[0].held) +
                              ".ffmpeg.log")


def test_review_repair_install_conflict(tmp_path: Path) -> None:
    """If the original name is taken meanwhile, the repair is dropped.

    Args:
        tmp_path: Temporary directory.
    """
    root = tmp_path / "root"
    result = make_file(tmp_path / "result.mp4", b"repaired")
    review, quarantine, _player = build_review(root, tmp_path, answers("a", "q"), result)
    make_file(root / "u" / "good.mp4", b"taken")
    assert not review.repair(quarantine.items[0])
    assert review.review_item(quarantine.items[1], "t") is False


def test_review_hopeless_one_by_one_delete_and_interrupt(tmp_path: Path) -> None:
    """Hopeless videos one by one with a confirmed delete; Ctrl+C returns 130.

    Args:
        tmp_path: Temporary directory.
    """
    root = tmp_path / "root"
    review, quarantine, _player = build_review(root, tmp_path,
                                               answers("s", "s", "o", "d", "y"))
    assert review.run() == 0
    assert quarantine.items[2].state == "deleted"

    review, quarantine, _player = build_review(tmp_path / "r2", tmp_path,
                                               answers("s", "s", "a", "n", "o", "q"))
    assert review.run() == 0 and quarantine.items[2].state == "open"
    review, quarantine, _player = build_review(tmp_path / "r3", tmp_path,
                                               answers("s", "s", "q"))
    assert review.run() == 0

    def interrupt(_question: str) -> str:
        raise KeyboardInterrupt

    review, quarantine, player = build_review(tmp_path / "r4", tmp_path,
                                              Prompt(input_func=interrupt))
    assert review.run() == 130 and player.closed == 1


class Terminal(io.StringIO):
    """Standard input double that claims to be a terminal."""

    def isatty(self) -> bool:
        """Claim a terminal.

        Returns:
            True.
        """
        return True


# -- command line ---------------------------------------------------------------------
@needs_ffmpeg
def test_cli_broken_collect_list_and_review(root: Path, tmp_path: Path, fake_player: str,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    """``broken --collect``, the listing and ``--review`` work end to end.

    Args:
        root: Shrink root fixture.
        tmp_path: Temporary directory.
        fake_player: Stand-in player.
        monkeypatch: Patcher.
    """
    report = str(tmp_path / "report.tsv")
    Config().set_value("broken", "backup", str(tmp_path / "backup"))
    Config().set_value("broken", "backup_of", str(tmp_path))
    monkeypatch.setattr(BackupMirror, "unavailable", lambda _self, _root: None)
    assert Cli().main(["broken", str(root), "--collect", "--dry-run", "--report", report]) == 0
    assert os.path.exists(root / "unit" / "Clips" / "cut.mp4")
    assert Cli().main(["broken", str(root), "--collect", "-y", "--report", report]) == 0
    assert not os.path.exists(root / "unit" / "Clips" / "cut.mp4")
    assert Cli().main(["broken", str(root)]) == 0
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    with pytest.raises(SystemExit):
        Cli().main(["broken", str(root), "--review"])
    # promising: keep; hopeless: delete all (input() reads sys.stdin)
    monkeypatch.setattr(sys, "stdin", Terminal("k\na\ny\n"))
    assert Cli().main(["broken", str(root), "--review", "--delete",
                       "--player", fake_player]) == 0
    assert os.path.exists(root / "unit" / "Clips" / "cut.mp4")
    assert os.path.exists(root / "unit" / "_flat" / "Clips_cut.mp4")
    assert not os.path.exists(root / "unit" / "Clips" / "grey.mp4")
    assert Cli().main(["broken", str(root), "--collect", "--review", "--delete",
                       "--player", fake_player]) == 0
