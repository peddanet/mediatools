"""Release 1.8.0: complete reports, all _broken folders, stored measurements, job display."""
import io
import json
import os
import re
import sys
from pathlib import Path

import pytest

from conftest import make_file
from test_release_170 import logger
from test_release_171 import BAD, GOOD, StubAssessor, mirror, quarantine_with
from mediatools.broken import (Assessment, AssessmentStore, BackupMirror, BackupResult,
                               BrokenCollector, BrokenFinder, BrokenReview, Quarantine,
                               QuarantineSet)
from mediatools.cli import Cli
from mediatools.core.config import Config
from mediatools.core.prompt import Prompt
from mediatools.gui.consoleview import ConsoleView
from mediatools.gui.shrinkmonitor import ShrinkMonitor
from mediatools.shrink.database import ShrinkDatabase
from mediatools.shrink.jobregistry import JobEntry, JobRegistry
from mediatools.shrink.jobspec import JobSpec
from mediatools.shrink.options import ShrinkOptions
from mediatools.shrink.shrinker import Shrinker

OLD = {"opened": True, "duration": 10, "decoded": 10, "still": 0, "errors": 12}
DAMAGED = Assessment(True, 10, 2, errors=40)
HEALTHY = Assessment(True, 10, 10)


class CountingAssessor(StubAssessor):
    """Assessor double that counts its measurements."""

    def __init__(self, results: dict[str, Assessment]) -> None:
        """Store the answers.

        Args:
            results: Assessment per file name.
        """
        super().__init__(results)
        self.calls = 0

    def assess(self, path: str) -> Assessment:
        """Count and answer.

        Args:
            path: Video.

        Returns:
            The stored answer.
        """
        self.calls += 1
        return super().assess(path)


def old_items(quarantine: Quarantine) -> None:
    """Mark the measurements of all items as made by method 1.

    Args:
        quarantine: Quarantine to change.
    """
    for item in quarantine.items:
        item.assessment = Assessment.from_dict(OLD)
    quarantine.save()


def broken_video(folder: Path, name: str) -> str:
    """Create a video recorded as BROKEN in its folder's database.

    Args:
        folder: Directory.
        name: File name.

    Returns:
        Its path.
    """
    path = make_file(folder / name, BAD)
    database = ShrinkDatabase(str(folder))
    database.ensure()
    database.record_failure(path, "BROKEN", "x")
    return path


# -- stored measurements -------------------------------------------------------------
def test_store_reuses_until_the_file_changes(tmp_path: Path) -> None:
    """Same size and time: no new decode; a change, --recheck or an old method: decode.

    Args:
        tmp_path: Temporary directory.
    """
    path = make_file(tmp_path / "v.mp4", BAD)
    file = str(tmp_path / "state" / AssessmentStore.FILE)
    counter = CountingAssessor({"v.mp4": DAMAGED})
    store = AssessmentStore(file, logger(tmp_path))
    assert store.assess(path, counter.assess) == DAMAGED and counter.calls == 1
    again = AssessmentStore(file, logger(tmp_path))
    assert again.assess(path, counter.assess) == DAMAGED and counter.calls == 1
    assert again.reused == 1 and oct(os.stat(file).st_mode & 0o777) == "0o600"
    assert AssessmentStore(file, logger(tmp_path), recheck=True).stored(path) is None
    make_file(tmp_path / "v.mp4", BAD + b"more")
    assert again.stored(path) is None
    again.assess(path, counter.assess)
    assert counter.calls == 2 and again.measured == 1
    data = json.loads(Path(file).read_text(encoding="utf-8"))
    data[os.path.abspath(path)]["assessment"] = OLD
    Path(file).write_text(json.dumps(data), encoding="utf-8")
    assert AssessmentStore(file, logger(tmp_path)).stored(path) is None
    data[os.path.abspath(path)] = {"size": "x"}
    Path(file).write_text(json.dumps(data), encoding="utf-8")
    assert AssessmentStore(file, logger(tmp_path)).stored(path) is None
    Path(file).write_text("{broken", encoding="utf-8")
    assert AssessmentStore(file, logger(tmp_path)).stored(path) is None
    Path(file).write_text("[]", encoding="utf-8")
    assert AssessmentStore(file, logger(tmp_path)).stored(path) is None


def test_store_without_file_and_missing_video(tmp_path: Path) -> None:
    """No file keeps nothing; a missing video is neither stored nor found.

    Args:
        tmp_path: Temporary directory.
    """
    store = AssessmentStore(None, logger(tmp_path))
    store.put(str(tmp_path / "gone.mp4"), HEALTHY)
    assert store.stored(str(tmp_path / "gone.mp4")) is None
    path = make_file(tmp_path / "v.mp4", BAD)
    store.put(path, HEALTHY)
    assert store.stored(path) is not None
    assert AssessmentStore.identity(str(tmp_path / "gone.mp4")) is None


# -- all _broken folders below a path ----------------------------------------------
def test_quarantine_set_finds_folders_below(tmp_path: Path) -> None:
    """The root's _broken first, then those below with a manifest; hidden ones are skipped.

    Args:
        tmp_path: Temporary directory.
    """
    quarantine_with(tmp_path, "a.mp4")                          # home/coll/_broken
    os.makedirs(tmp_path / "home" / "coll" / "x" / "_broken")    # no manifest: ignored
    make_file(tmp_path / "home" / ".hidden" / "_broken" / "manifest.jsonl", b"")
    found = QuarantineSet(str(tmp_path / "home"), logger(tmp_path))
    roots = [str(tmp_path / "home"), str(tmp_path / "home" / "coll")]
    assert [m.root for m in found.members] == roots
    assert len(found.open_items()) == 1
    assert found.holds(str(tmp_path / "home" / "coll" / "u" / "a.mp4"))
    assert found.describe() == ["_broken: 0 video(s) set aside",
                                "coll/_broken: 1 video(s) set aside"]


def test_collect_on_parent_skips_videos_held_below(tmp_path: Path) -> None:
    """A video set aside in a sub-folder is not collected again by a run on the parent.

    Args:
        tmp_path: Temporary directory.
    """
    sub = quarantine_with(tmp_path, "a.mp4")
    home = tmp_path / "home"
    broken_video(home / "coll" / "u", "a.mp4")       # name back in place, still recorded
    broken_video(home / "coll" / "u", "b.mp4")
    found = QuarantineSet(str(home), logger(tmp_path))
    counter = CountingAssessor({"a.mp4": DAMAGED, "b.mp4": DAMAGED})
    collector = BrokenCollector(found.primary, BrokenFinder(str(home)), counter,
                                quarantines=found)
    planned = collector.plan()
    assert [os.path.basename(path) for path, _reason, _a in planned] == ["b.mp4"]
    assert counter.calls == 1 and len(sub.items) == 1


# -- complete reporting and dry run ----------------------------------------------------
def test_remeasure_reports_every_result(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Damaged and healthy results are both reported; a dry run stores nothing.

    Args:
        tmp_path: Temporary directory.
        capsys: Output capture.
    """
    quarantine = quarantine_with(tmp_path, "bad.mp4", "fine.mp4")
    old_items(quarantine)
    assessor = StubAssessor({"bad.mp4": DAMAGED, "fine.mp4": HEALTHY})
    collector = BrokenCollector(quarantine, BrokenFinder(quarantine.root), assessor)
    collector.remeasure(dry_run=True)
    text = capsys.readouterr().out
    assert "re-measured 1/2: damaged, promising" in text
    assert "re-measured 2/2: not damaged" in text and "would go back to shrink" in text
    assert all(item.state == "open" for item in Quarantine(quarantine.root,
                                                           logger(tmp_path)).items)
    collector.remeasure(dry_run=False)
    states = [item.state for item in Quarantine(quarantine.root, logger(tmp_path)).items]
    assert states == ["open", "released"]


def test_dry_run_previews_backup_of_set_aside_videos(tmp_path: Path,
                                                     capsys: pytest.CaptureFixture[str]) -> None:
    """A dry run without new finds still checks the set-aside videos, storing nothing.

    Args:
        tmp_path: Temporary directory.
        capsys: Output capture.
    """
    quarantine = quarantine_with(tmp_path, "a.mp4")
    make_file(tmp_path / "backup" / "coll" / "u" / "a.mp4", GOOD)
    collector = BrokenCollector(quarantine, BrokenFinder(quarantine.root),
                                StubAssessor({}), mirror(tmp_path))
    assert collector.run(dry_run=True) == 0
    out = capsys.readouterr().out
    assert "backup preview for 1 video(s)" in out and "intact copy in the backup" in out
    assert Quarantine(quarantine.root, logger(tmp_path)).items[0].backup is None
    collector.preview_backup([])                   # nothing pending without a backup check
    assert BrokenCollector(quarantine, BrokenFinder(quarantine.root), StubAssessor({}),
                           None).preview_backup([]) is None


def test_backup_unavailable_in_preview(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Without the disk the preview says why.

    Args:
        tmp_path: Temporary directory.
        capsys: Output capture.
    """
    quarantine = quarantine_with(tmp_path, "a.mp4")
    collector = BrokenCollector(quarantine, BrokenFinder(quarantine.root),
                                StubAssessor({}), mirror(tmp_path))
    collector.preview_backup([])
    assert "backup not checked" in capsys.readouterr().out


def test_collect_without_new_finds_checks_the_backup(tmp_path: Path) -> None:
    """A real run without new finds stores the backup result of set-aside videos.

    Args:
        tmp_path: Temporary directory.
    """
    quarantine = quarantine_with(tmp_path, "a.mp4")
    make_file(tmp_path / "backup" / "coll" / "u" / "a.mp4", GOOD)
    collector = BrokenCollector(quarantine, BrokenFinder(quarantine.root),
                                StubAssessor({}), mirror(tmp_path))
    assert collector.run(dry_run=False) == 0
    assert Quarantine(quarantine.root, logger(tmp_path)).items[0].backup is not None


# -- backup check only where needed ---------------------------------------------------
def test_needs_backup_check_rules(tmp_path: Path) -> None:
    """No result, --recheck or a changed copy need a check; a fresh result does not.

    Args:
        tmp_path: Temporary directory.
    """
    quarantine = quarantine_with(tmp_path, "a.mp4")
    item = quarantine.items[0]
    plain = BrokenCollector(quarantine, BrokenFinder(quarantine.root), StubAssessor({}))
    again = BrokenCollector(quarantine, BrokenFinder(quarantine.root), StubAssessor({}),
                            recheck=True)
    assert plain.needs_backup_check(item)
    copy = make_file(tmp_path / "backup" / "coll" / "u" / "a.mp4", GOOD)
    item.backup = BackupResult("intact", copy, "mirror")
    assert not plain.needs_backup_check(item) and again.needs_backup_check(item)
    item.backup = BackupResult("intact", copy, "mirror", checked="2000-01-01T00:00:00")
    assert plain.needs_backup_check(item)
    assert not BrokenCollector.copy_changed("", "x")
    assert not BrokenCollector.copy_changed(str(tmp_path / "gone"), "2000-01-01T00:00:00")
    item.assessment = HEALTHY
    assert not again.needs_backup_check(item)


def test_check_backup_stops_when_disk_missing(tmp_path: Path,
                                              capsys: pytest.CaptureFixture[str]) -> None:
    """Without the disk the check is reported as pending.

    Args:
        tmp_path: Temporary directory.
        capsys: Output capture.
    """
    quarantine = quarantine_with(tmp_path, "a.mp4")
    collector = BrokenCollector(quarantine, BrokenFinder(quarantine.root),
                                StubAssessor({}), mirror(tmp_path))
    collector.check_backup()
    assert "backup check pending" in capsys.readouterr().out


def test_cli_parent_lists_and_checks_all_folders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                 capsys: pytest.CaptureFixture[str]) -> None:
    """Listing, backup check and dry-run notes on a parent folder.

    Args:
        tmp_path: Temporary directory.
        monkeypatch: Patcher.
        capsys: Output capture.
    """
    quarantine_with(tmp_path, "a.mp4")
    home = tmp_path / "home"
    assert Cli().main(["broken", str(home)]) == 0
    out = capsys.readouterr().out
    assert "coll/_broken: 1 video(s) set aside" in out and "in 2 _broken folder(s)" in out
    Config().set_value("broken", "backup", str(tmp_path / "backup"))
    Config().set_value("broken", "backup_of", str(home))
    monkeypatch.setattr(BackupMirror, "unavailable", lambda _self, _root: None)
    monkeypatch.setattr(BackupMirror, "_judge",
                        lambda _self, copies, how: BackupResult("intact", copies[0], how))
    make_file(tmp_path / "backup" / "coll" / "u" / "a.mp4", GOOD)
    assert Cli().main(["broken", str(home), "--backup-check"]) == 0
    assert Quarantine(str(home / "coll"), logger(tmp_path)).items[0].backup is not None
    assert Cli().main(["broken", str(home), "--backup-check"]) == 0
    assert "already has a result" in capsys.readouterr().out
    monkeypatch.setattr(Cli, "_need", lambda *_args: None)
    assert Cli().main(["broken", str(home), "--collect", "--dry-run", "--review",
                       "--backup-check"]) == 0
    assert "--backup-check and --review not carried out" in capsys.readouterr().out
    assert Cli().main(["broken", str(home), "--backup-check", "--recheck"]) == 0


def test_review_runs_through_all_folders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review takes every folder with open videos and stops on quit.

    Args:
        tmp_path: Temporary directory.
        monkeypatch: Patcher.
    """
    quarantine_with(tmp_path, "a.mp4")
    seen: list[str] = []

    def fake_run(self: BrokenReview) -> int:
        """Record the folder and quit.

        Args:
            self: The review.

        Returns:
            0.
        """
        seen.append(self.quarantine.root)
        self.stopped = True
        return 0

    monkeypatch.setattr(BrokenReview, "run", fake_run)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    monkeypatch.setenv(Prompt.GUI_ENV, "1")
    monkeypatch.setattr(Cli, "_need", lambda *_args: None)
    assert Cli().main(["broken", str(tmp_path / "home"), "--review", "--mute"]) == 0
    assert seen == [str(tmp_path / "home" / "coll")]


# -- shrink: the job as it runs ---------------------------------------------------------
def test_stored_job_keeps_retry_and_keep_copies(tmp_path: Path) -> None:
    """Both options survive the log line and come back in the command line.

    Args:
        tmp_path: Temporary directory.
    """
    settings: dict[str, object] = {"retry_failed": True, "keep_copies": True, "jobs": 2}
    entry = JobEntry(JobSpec(str(tmp_path)), settings)
    assert entry.argv()[-3:] == ["2", "--retry-failed", "--keep-copies"]
    log = tmp_path / "shrink.log"
    job = {**JobSpec(str(tmp_path)).as_dict(), "retry_failed": True, "jobs": 2}
    log.write_text("x shrink job: " + json.dumps(job) + "\n", encoding="utf-8")
    found = JobRegistry.from_log(str(log))
    assert found is not None and found[1]["retry_failed"] is True
    line = ShrinkMonitor.job_line(str(log))
    assert line.split()[:2] == ["job:", str(tmp_path)] and "--retry-failed" in line
    assert ShrinkMonitor.job_line(None) == ""
    assert ShrinkMonitor.job_line(str(tmp_path / "none.log")) == ""
    settings = Shrinker(str(tmp_path), logger(tmp_path),
                        opts=ShrinkOptions(retry_failed=True)).settings()
    assert settings["retry_failed"] is True and settings["keep_copies"] is False


def test_console_names_a_job_handed_to_the_background() -> None:
    """The status line says that the job runs on after its launcher ended."""

    class Pane:  # pylint: disable=too-few-public-methods
        """Minimal stand-in with the attributes ``finished`` reads."""

        BACKGROUND = ConsoleView.BACKGROUND
        tail = "shrink continues in the background (pid 4711); the terminal may be closed."

    assert "runs in the background (pid 4711)" in ConsoleView.finished(Pane(), 0)  # type: ignore
    assert ConsoleView.finished(Pane(), 1) == "finished, exit code 1"  # type: ignore
    Pane.tail = "done"
    assert ConsoleView.finished(Pane(), 0) == "finished, exit code 0"  # type: ignore
    assert re.search(ConsoleView.BACKGROUND, "broken continues in the background (pid 1)")


def test_retry_failed_warns_about_broken_videos(tmp_path: Path,
                                                capsys: pytest.CaptureFixture[str]) -> None:
    """A planned BROKEN video under --retry-failed is announced.

    Args:
        tmp_path: Temporary directory.
        capsys: Output capture.
    """
    shrinker = Shrinker(str(tmp_path), logger(tmp_path), opts=ShrinkOptions(retry_failed=True))
    shrinker.stats["retry_broken"] = 2
    shrinker._report_plan([], 0, None)                   # pylint: disable=protected-access
    assert "2 video(s) recorded as BROKEN" in capsys.readouterr().out
