"""Release 1.7.1: backup check before repair/delete, false alarms, GUI questions, reload answer."""
import io
import json
import os
import sys
import threading
import time
import tkinter as tk
from pathlib import Path

import pytest

from conftest import make_file, needs_ffmpeg
from test_gui_tk import fixture_root  # noqa: F401  # pylint: disable=unused-import
from test_release_170 import StubPlayer, StubRepairer, answers, logger, video
from mediatools.broken import (Assessment, Assessor, BackupMirror, BackupResult,
                               BrokenCollector, BrokenFinder, BrokenReview,
                               DownloaderDatabase, Quarantine)
from mediatools.cli import Cli
from mediatools.core.config import Config
from mediatools.core.decodecheck import DecodeCheck
from mediatools.core.prompt import Prompt
from mediatools.core.runcontrol import RunControl
from mediatools.core.runstatus import RunStatus
from mediatools.core.selfrestart import SelfRestart
from mediatools.gui.choicedialog import ChoiceDialog
from mediatools.gui.consoleview import ConsoleView
from mediatools.gui.jobrunner import JobRunner
from mediatools.shrink.database import ShrinkDatabase
from mediatools.triage.player import PlayerSession
from mediatools.triage.remover import FileRemover

GOOD = b"good video data"
BAD = b"bad video data"


class FakeDecode(DecodeCheck):
    """Decode double: files starting with ``good`` decode cleanly."""

    def verify(self, path: str, timeout: float, niceness: int = 19) -> str | None:
        """Judge a file by its content.

        Args:
            path: File.
            timeout: Ignored.
            niceness: Ignored.

        Returns:
            None for good data, else a reason.
        """
        del timeout, niceness
        return None if Path(path).read_bytes().startswith(b"good") else "3 decoder error line(s)"


class FakeProbe:
    """Probe double: every file lasts 5 s."""

    @staticmethod
    def duration(_path: str) -> float:
        """Report 5 s.

        Returns:
            5.0.
        """
        return 5.0


class OtherDisk(BackupMirror):
    """Backup mirror whose backup directory counts as another disk."""

    def device(self, path: str) -> int:  # pylint: disable=arguments-differ
        """Give the backup its own device number.

        Args:
            path: Path.

        Returns:
            1 for the backup, 0 otherwise.
        """
        return 1 if self.backup and path.startswith(self.backup) else 0


class StubAssessor(Assessor):
    """Assessor double with a fixed answer per file name."""

    def __init__(self, results: dict[str, Assessment]) -> None:
        """Store the answers.

        Args:
            results: Assessment per file name.
        """
        super().__init__()
        self.results = results

    def assess(self, path: str) -> Assessment:
        """Return the stored answer.

        Args:
            path: Video.

        Returns:
            The assessment for its name.
        """
        return self.results[os.path.basename(path)]


def mirror(tmp_path: Path) -> OtherDisk:
    """Return a backup mirror of ``tmp_path/home`` in ``tmp_path/backup``.

    Args:
        tmp_path: Temporary directory.

    Returns:
        The mirror with doubles for decoding and probing.
    """
    return OtherDisk(str(tmp_path / "backup"), str(tmp_path / "home"), logger(tmp_path),
                     FakeDecode(), FakeProbe())  # type: ignore[arg-type]


OLD = {"opened": True, "duration": 10, "decoded": 10, "still": 0, "errors": 12}


# -- detection ----------------------------------------------------------------------
def test_null_muxer_messages_are_ignored() -> None:
    """Time stamp messages of the null muxer are no decoder errors."""
    lines = ["[null @ 0x1] Application provided invalid, non monotonically increasing dts",
             "", "[h264 @ 0x2] decode_slice_header error"]
    assert DecodeCheck.relevant(lines) == ["[h264 @ 0x2] decode_slice_header error"]


@needs_ffmpeg
def test_decode_check_counts_without_muxer_lines(tmp_path: Path) -> None:
    """A clean video has no relevant error lines.

    Args:
        tmp_path: Temporary directory.
    """
    clean = video(tmp_path / "clean.mp4", seconds=2)
    assert DecodeCheck().errors(clean) == 0 and DecodeCheck().verify(clean, 60) is None


def test_assessment_healthy_and_method() -> None:
    """Healthy needs a full clean decode and a moving picture; old records are method 1."""
    assert Assessment(True, 10, 10).healthy and Assessment(True, 10, 10).current
    assert not Assessment(True, 10, 10, errors=1).healthy
    assert not Assessment(True, 10, 9).healthy
    assert not Assessment(True, 10, 10, still=10).healthy
    old = Assessment.from_dict(OLD)
    assert old.method == 1 and not old.current
    assert Assessment.from_dict(Assessment(True, 5, 5).to_dict()).current


# -- backup -------------------------------------------------------------------------
def test_backup_result_texts_and_records() -> None:
    """Summaries and manifest records of the three states."""
    assert "same path" in BackupResult("intact", "/b/x", "mirror").summary()
    assert "elsewhere" in BackupResult("damaged", "/b/x", "search", "bad").summary()
    assert BackupResult("missing").summary() == "no copy in the backup"
    assert BackupResult.from_dict(None) is None
    again = BackupResult.from_dict(BackupResult("intact", "/b/x", "mirror").to_dict())
    assert again is not None and again.intact


def test_backup_mirror_paths_and_availability(tmp_path: Path) -> None:
    """Mirror paths; every reason why the backup cannot be checked.

    Args:
        tmp_path: Temporary directory.
    """
    log = logger(tmp_path)
    root = tmp_path / "home" / "coll"
    os.makedirs(root)
    assert "no backup configured" in str(BackupMirror(None, str(tmp_path), log)
                                         .unavailable(str(root)))
    assert BackupMirror(None, str(tmp_path), log).mirror(str(root)) is None
    backup = mirror(tmp_path)
    assert backup.mirror(str(root / "a.mp4")) == str(tmp_path / "backup" / "coll" / "a.mp4")
    assert backup.mirror(str(tmp_path / "home")) is None
    assert backup.mirror(str(tmp_path / "elsewhere.mp4")) is None
    assert "not mounted" in str(backup.unavailable(str(root)))
    os.makedirs(tmp_path / "backup")
    same = BackupMirror(str(tmp_path / "backup"), str(tmp_path / "home"), log)
    assert "same disk" in str(same.unavailable(str(root)))
    assert "mirror of the shrink root is missing" in str(backup.unavailable(str(root)))
    assert "not below" in str(backup.unavailable(str(tmp_path)))
    os.makedirs(tmp_path / "backup" / "coll")
    assert backup.unavailable(str(root)) is None
    assert "not readable" in str(same.unavailable(str(tmp_path / "gone")))
    assert not backup._index({"x"})                   # pylint: disable=protected-access
    assert not BackupMirror(None, str(tmp_path), log)._index({"x"})  # pylint: disable=W0212


def test_backup_check_mirror_search_damaged_missing(tmp_path: Path) -> None:
    """Same path first, then by name; damaged copies lose to an intact one.

    Args:
        tmp_path: Temporary directory.
    """
    home, bk = tmp_path / "home" / "coll", tmp_path / "backup" / "coll"
    make_file(bk / "a" / "same.mp4", GOOD)                    # intact at the same path
    make_file(bk / "a" / "moved.mp4", BAD)                    # damaged at the same path ...
    make_file(bk / "old" / "moved.mp4", GOOD)                 # ... intact elsewhere
    make_file(bk / "old" / "lost.mp4", BAD)                   # only a damaged copy elsewhere
    make_file(bk / "a" / "empty.mp4", b"")
    names = {str(home / "a" / name): [str(home / "a" / name)]
             for name in ("same.mp4", "moved.mp4", "lost.mp4", "none.mp4", "empty.mp4")}
    results = mirror(tmp_path).check(names)
    state = {os.path.basename(key): (value.state, value.found_by) for key, value in results.items()}
    assert state == {"same.mp4": ("intact", "mirror"), "moved.mp4": ("intact", "search"),
                     "lost.mp4": ("damaged", "search"), "none.mp4": ("missing", ""),
                     "empty.mp4": ("damaged", "mirror")}
    assert results[str(home / "a" / "empty.mp4")].note == "empty file"
    assert mirror(tmp_path)._size(str(tmp_path / "nope")) == 0  # pylint: disable=W0212


# -- quarantine -----------------------------------------------------------------------
def quarantine_with(tmp_path: Path, *names: str) -> Quarantine:
    """Set plain files aside below ``home/coll``.

    Args:
        tmp_path: Temporary directory.
        names: File names below ``u``.

    Returns:
        The quarantine.
    """
    root = tmp_path / "home" / "coll"
    quarantine = Quarantine(str(root), logger(tmp_path))
    for name in names:
        path = make_file(root / "u" / name, BAD)
        ShrinkDatabase(str(root / "u")).ensure()
        ShrinkDatabase(str(root / "u")).record_failure(path, "BROKEN", "x")
        quarantine.collect(path, Assessment(True, 10, 9, errors=3), "x")
    return quarantine


def test_release_reassess_and_recover(tmp_path: Path) -> None:
    """Released videos go to shrink; class changes move; recovery takes every name.

    Args:
        tmp_path: Temporary directory.
    """
    root = tmp_path / "home" / "coll"
    quarantine = quarantine_with(tmp_path, "ok.mp4", "grey.mp4", "rec.mp4")
    ok, grey, rec = quarantine.items
    assert quarantine.release(ok) and ok.state == "released"
    assert ShrinkDatabase(str(root / "u")).failure(str(root / "u" / "ok.mp4")) is None
    quarantine.reassess(grey, Assessment(False))
    assert grey.kind == "hopeless" and "hoffnungslos" in grey.held and os.path.exists(grey.held)
    quarantine.reassess(grey, Assessment(False, note="again"))
    assert not quarantine.recover(rec, FileRemover(True))           # nothing recorded
    os.link(rec.held, str(root / "u" / "rec2.mp4"))
    os.rename(str(root / "u" / "rec2.mp4"), rec.held + ".2")
    rec.names.append([str(root / "u" / "rec2.mp4"), rec.held + ".2"])
    source = make_file(tmp_path / "backup" / "coll" / "u" / "rec.mp4", GOOD)
    quarantine.note_backup(rec, BackupResult("intact", source, "mirror"))
    make_file(root / "u" / "rec.mp4", b"taken")
    assert not quarantine.recover(rec, FileRemover(True))           # name taken
    os.remove(root / "u" / "rec.mp4")
    assert quarantine.recover(rec, FileRemover(True)) and rec.state == "recovered"
    assert Path(root / "u" / "rec.mp4").read_bytes() == GOOD
    assert os.path.samefile(root / "u" / "rec.mp4", root / "u" / "rec2.mp4")
    assert ShrinkDatabase(str(root / "u")).failure(str(root / "u" / "rec.mp4")) is None
    reread = Quarantine(str(root), logger(tmp_path))
    assert reread.items[2].backup is not None and reread.items[2].backup.intact


def test_recover_copy_failure_and_release_conflict(tmp_path: Path) -> None:
    """A failed copy leaves nothing behind; a taken name blocks the release.

    Args:
        tmp_path: Temporary directory.
    """
    root = tmp_path / "home" / "coll"
    quarantine = quarantine_with(tmp_path, "a.mp4", "b.mp4")
    first, second = quarantine.items
    quarantine.note_backup(first, BackupResult("intact", str(tmp_path / "gone.mp4"), "mirror"))
    assert not quarantine.recover(first, FileRemover(True))
    assert not any(name.endswith(".recover") for name in os.listdir(root / "u"))
    make_file(root / "u" / "b.mp4", b"taken")
    assert not quarantine.release(second) and second.state == "open"


def test_recover_detects_short_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A copy with another size than the backup is dropped.

    Args:
        tmp_path: Temporary directory.
        monkeypatch: Patcher.
    """
    quarantine = quarantine_with(tmp_path, "a.mp4")
    item = quarantine.items[0]
    source = make_file(tmp_path / "backup" / "a.mp4", GOOD)
    quarantine.note_backup(item, BackupResult("intact", source, "mirror"))
    real = os.path.getsize
    monkeypatch.setattr(os.path, "getsize", lambda p: 1 if p.endswith(".recover") else real(p))
    assert not quarantine.recover(item, FileRemover(True)) and item.state == "open"


# -- collector ----------------------------------------------------------------------
def test_collector_false_alarms_remeasure_and_backup(tmp_path: Path) -> None:
    """False alarms lose their entry; old measurements are renewed; backup checked after moving.

    Args:
        tmp_path: Temporary directory.
    """
    root = tmp_path / "home" / "coll"
    quarantine = quarantine_with(tmp_path, "oldok.mp4", "oldbad.mp4")
    for item in quarantine.items:
        item.assessment = Assessment.from_dict(OLD)
    quarantine.save()
    database = ShrinkDatabase(str(root / "u"))
    for name in ("fine.mp4", "dead.mp4"):
        database.record_failure(make_file(root / "u" / name, BAD), "BROKEN", "x")
    make_file(tmp_path / "backup" / "coll" / "u" / "dead.mp4", GOOD)
    os.makedirs(tmp_path / "backup" / "coll", exist_ok=True)
    assessor = StubAssessor({"oldok.mp4": Assessment(True, 10, 10),
                             "oldbad.mp4": Assessment(False),
                             "fine.mp4": Assessment(True, 10, 10),
                             "dead.mp4": Assessment(True, 10, 5, errors=4)})
    collector = BrokenCollector(quarantine, BrokenFinder(str(root)), assessor,
                                mirror(tmp_path), answers("y"))
    assert collector.run(dry_run=True) == 0
    assert database.failure(str(root / "u" / "fine.mp4")) is not None
    assert os.path.exists(root / "u" / "dead.mp4")
    assert collector.run() == 0
    states = {os.path.basename(item.original): item.state for item in quarantine.items}
    assert states == {"oldok.mp4": "released", "oldbad.mp4": "open", "dead.mp4": "open"}
    assert ShrinkDatabase(str(root / "u")).failure(str(root / "u" / "fine.mp4")) is None
    dead = quarantine.items[-1]
    assert dead.backup is not None and dead.backup.intact
    assert quarantine.items[1].kind == "hopeless" and quarantine.items[1].backup is not None


def test_collector_backup_pending_and_without_backup(tmp_path: Path) -> None:
    """Unavailable backup: the check is announced; no backup: skipped.

    Args:
        tmp_path: Temporary directory.
    """
    root = tmp_path / "home" / "coll"
    quarantine = quarantine_with(tmp_path, "a.mp4")
    database = ShrinkDatabase(str(root / "u"))
    database.record_failure(make_file(root / "u" / "b.mp4", BAD), "BROKEN", "x")
    assessor = StubAssessor({"b.mp4": Assessment(True, 10, 5, errors=4)})
    collector = BrokenCollector(quarantine, BrokenFinder(str(root)), assessor,
                                mirror(tmp_path), answers("y"))
    assert collector.run(dry_run=True) == 0 and collector.run() == 0
    assert all(item.backup is None for item in quarantine.items)
    BrokenCollector(quarantine, BrokenFinder(str(root)), assessor).check_backup()
    BrokenCollector(quarantine, BrokenFinder(str(root)), assessor).preview_backup(["x"])
    os.makedirs(tmp_path / "backup" / "coll")
    for item in quarantine.items:
        quarantine.note_backup(item, BackupResult("missing"))
    collector.check_backup()                                     # nothing left to check


# -- review -------------------------------------------------------------------------
def review_for(tmp_path: Path, quarantine: Quarantine, prompt: Prompt,
               backup: BackupMirror | None = None) -> tuple[BrokenReview, StubPlayer]:
    """Wire a review with doubles.

    Args:
        tmp_path: Temporary directory.
        quarantine: The quarantine.
        prompt: Scripted prompt.
        backup: Backup mirror; the default test mirror if None.

    Returns:
        ``(review, player)``.
    """
    log = logger(tmp_path)
    player = StubPlayer()
    review = BrokenReview(quarantine, player, FileRemover(True, log),  # type: ignore[arg-type]
                          StubRepairer(None), DownloaderDatabase("", log),
                          backup or mirror(tmp_path), StubAssessor({}), prompt)
    return review, player


def test_review_choices_follow_measurement_and_backup(tmp_path: Path) -> None:
    """Healthy: keep; intact copy: recover; unchecked: check; missing: repair or delete.

    Args:
        tmp_path: Temporary directory.
    """
    quarantine = quarantine_with(tmp_path, "a.mp4")
    item = quarantine.items[0]
    review, _player = review_for(tmp_path, quarantine, answers())
    assert review.choices(item) == (["p", "c", "k", "s", "q"], "c")
    item.backup = BackupResult("intact", "/x", "mirror")
    assert review.choices(item)[1] == "b"
    item.backup = BackupResult("missing")
    assert review.choices(item) == (["p", "r", "k", "d", "s", "q"], "r")
    item.assessment = Assessment(True, 10, 2, errors=4)
    assert review.choices(item)[1] == "p"
    item.assessment = Assessment(True, 10, 10)
    assert review.choices(item) == (["p", "k", "s", "q"], "k")


def test_review_check_recover_keep_and_refused_delete(tmp_path: Path) -> None:
    """Check asks for the disk, then finds the copy; recover; healthy keep releases.

    Args:
        tmp_path: Temporary directory.
    """
    root = tmp_path / "home" / "coll"
    quarantine = quarantine_with(tmp_path, "a.mp4", "b.mp4")
    first, second = quarantine.items
    review, player = review_for(tmp_path, quarantine,
                                answers("c", "c", "p", "", "b", "s"))
    assert not review.delete(first)                               # not checked yet
    make_file(tmp_path / "backup" / "coll" / "u" / "a.mp4", GOOD)
    os.makedirs(tmp_path / "backup" / "coll" / "u", exist_ok=True)
    os.rename(tmp_path / "backup", tmp_path / "away")              # disk not mounted
    scripted = review.prompt.input

    def remount(question: str) -> str:
        """Answer the script; mount the disk after the first failed check.

        Args:
            question: Prompt text.

        Returns:
            The scripted answer.
        """
        answer = scripted(question)
        if first.backup is None and not os.path.exists(tmp_path / "backup") \
                and os.path.exists(tmp_path / "away") and answer == "c" and remount.calls:
            os.rename(tmp_path / "away", tmp_path / "backup")
        remount.calls += 1
        return answer

    remount.calls = 0                                               # type: ignore[attr-defined]
    review.prompt.input = remount
    assert review.review_item(first, "t")
    assert first.state == "recovered" and Path(root / "u" / "a.mp4").read_bytes() == GOOD
    assert player.closed == 1
    assert not review.delete(first)                               # intact copy recorded
    second.assessment = Assessment(True, 10, 10)
    assert review.keep(second) and second.state == "released"


def test_review_run_remeasures_and_groups(tmp_path: Path) -> None:
    """Old measurements are renewed; the hopeless group can check, recover and delete.

    Args:
        tmp_path: Temporary directory.
    """
    root = tmp_path / "home" / "coll"
    quarantine = quarantine_with(tmp_path, "h1.mp4", "h2.mp4", "p1.mp4")
    for item in quarantine.items:
        item.assessment = Assessment.from_dict(OLD)
    quarantine.save()
    make_file(tmp_path / "backup" / "coll" / "u" / "h1.mp4", GOOD)
    review, _player = review_for(tmp_path, quarantine, answers("s", "c", "b", "a", "y"))
    review.assessor = StubAssessor({"h1.mp4": Assessment(False), "h2.mp4": Assessment(False),
                                    "p1.mp4": Assessment(True, 10, 9, errors=2)})
    assert review.run() == 0
    states = {os.path.basename(item.original): item.state for item in quarantine.items}
    assert states == {"h1.mp4": "recovered", "h2.mp4": "deleted", "p1.mp4": "open"}
    assert Path(root / "u" / "h1.mp4").read_bytes() == GOOD
    unavailable, _player = review_for(tmp_path, quarantine, answers(),
                                      BackupMirror(None, str(tmp_path), logger(tmp_path)))
    assert not unavailable.check_backup(quarantine.open_items())


def test_review_progress_steps(capsys: pytest.CaptureFixture[str]) -> None:
    """Repair progress is shown in steps of 5 %.

    Args:
        capsys: Output capture.
    """
    shown = [-1]
    for fraction in (0.0, 0.01, 0.06, 0.07, 1.0):
        BrokenReview._progress(fraction, shown)                    # pylint: disable=W0212
    assert capsys.readouterr().out.count("repairing") == 3


# -- prompt, player -----------------------------------------------------------------
def test_prompt_choose_pause_and_gui_announcement(monkeypatch: pytest.MonkeyPatch,
                                                  capsys: pytest.CaptureFixture[str]) -> None:
    """Choices loop on wrong input, end at the end of input, and are announced for the GUI.

    Args:
        monkeypatch: Patcher.
        capsys: Output capture.
    """
    prompt = answers("x", "", "B")
    assert prompt.choose("Q", [("a", "one"), ("b", "two")], "a", "b") == "a"
    assert prompt.choose("", [("a", "one"), ("b", "two")], "a", "b") == "b"

    def ended(_question: str) -> str:
        raise EOFError

    closed = Prompt(input_func=ended)
    assert closed.choose("Q", [("a", "one"), ("b", "two")], "a", "b") == "b"
    assert closed.ended and closed.text("Name", "d") == "d"
    assert "Please answer a or b." in capsys.readouterr().out
    monkeypatch.setenv(Prompt.GUI_ENV, "1")
    gui = answers("", "", "y")
    gui.pause("seen")
    assert gui.choose("Q", [("a", "one")], "a", "a") == "a" and gui.yes_no("Sure?")
    lines = [line for line in capsys.readouterr().out.splitlines()
             if line.startswith(Prompt.MARKER)]
    specs = [json.loads(line[len(Prompt.MARKER):]) for line in lines]
    assert [spec["default"] for spec in specs] == ["", "a", "n"]
    assert specs[2]["choices"] == [["y", "yes"], ["n", "no"]]


def test_player_start_volume() -> None:
    """With sound the players start at the given volume."""
    assert "--gain=0.30" in PlayerSession.build_argv("vlc", True, 30)
    assert "--no-audio" in PlayerSession.build_argv("vlc", False, 30)
    assert "--volume=25" in PlayerSession.build_argv("mpv", True, 25)
    assert not any(a.startswith("--gain") for a in PlayerSession.build_argv("vlc", True))
    assert not any(a.startswith("--volume") for a in PlayerSession.build_argv("mpv", True))


# -- reload answer ------------------------------------------------------------------
def test_reload_answer_in_reload_and_status(monkeypatch: pytest.MonkeyPatch,
                                            capsys: pytest.CaptureFixture[str]) -> None:
    """The job's answer reaches ``mediatools reload`` and ``status``.

    Args:
        monkeypatch: Patcher.
        capsys: Output capture.
    """
    control = RunControl("shrink")
    assert Cli.cmd_reload(None, wait=0.1) == 1                      # type: ignore[arg-type]
    control.start()
    assert Cli.cmd_reload(None, wait=0.6) == 0                      # type: ignore[arg-type]
    assert "no answer within" in capsys.readouterr().out

    def answer(text: str) -> None:
        time.sleep(0.3)
        control.answer_restart(text)

    for text, code in (("accepted: switching from 1 to 2", 0),
                       ("rejected: mediatools 2 is already running", 1)):
        thread = threading.Thread(target=answer, args=(text,))
        thread.start()
        assert Cli.cmd_reload(None, wait=5) == code                 # type: ignore[arg-type]
        thread.join()
        assert text in capsys.readouterr().out
    monkeypatch.setattr(RunStatus, "running_version", lambda _self, _log: ("2", "now"))
    assert any(line.startswith("reload:    ") and "rejected" in line
               for line in RunStatus(control).lines())
    control.finish()


def test_self_restart_reports_why_it_fails(tmp_path: Path) -> None:
    """The reason of a failing version query is kept.

    Args:
        tmp_path: Temporary directory.
    """
    broken = make_file(tmp_path / "py", b"#!/bin/sh\necho oops >&2\nexit 3\n")
    os.chmod(broken, 0o755)
    restart = SelfRestart([], "log", python=broken)
    assert restart.installed_version() is None and "exit code 3: oops" in restart.last_error
    missing = SelfRestart([], "log", python=str(tmp_path / "nothing"))
    assert missing.installed_version() is None and "nothing" in missing.last_error


# -- command line -------------------------------------------------------------------
def test_cli_broken_options(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                            capsys: pytest.CaptureFixture[str]) -> None:
    """Dry run needs --collect; backup check; listing hint; review from the GUI.

    Args:
        tmp_path: Temporary directory.
        monkeypatch: Patcher.
        capsys: Output capture.
    """
    root = tmp_path / "home" / "coll"
    quarantine = quarantine_with(tmp_path, "a.mp4")
    with pytest.raises(SystemExit):
        Cli().main(["broken", str(root), "--dry-run"])
    assert Cli().main(["broken", str(root)]) == 0
    assert "nothing measured or moved" in capsys.readouterr().out
    assert Cli().main(["broken", str(root), "--backup-check"]) == 1
    Config().set_value("broken", "backup", str(tmp_path / "backup"))
    Config().set_value("broken", "backup_of", str(tmp_path / "home"))
    monkeypatch.setattr(BackupMirror, "unavailable", lambda _self, _root: None)
    monkeypatch.setattr(BackupMirror, "_judge",
                        lambda _self, copies, how: BackupResult("intact", copies[0], how))
    make_file(tmp_path / "backup" / "coll" / "u" / "a.mp4", GOOD)
    assert Cli().main(["broken", str(root), "--backup-check"]) == 0
    assert Quarantine(str(root), logger(tmp_path)).items[0].backup is not None
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    monkeypatch.setenv(Prompt.GUI_ENV, "1")
    monkeypatch.setattr(Cli, "_need", lambda *_args: None)
    assert Cli().main(["broken", str(root), "--backup-check", "--review", "--mute"]) == 0
    del quarantine


# -- GUI ----------------------------------------------------------------------------
def test_job_runner_announces_gui(monkeypatch: pytest.MonkeyPatch) -> None:
    """Commands started by the GUI see MEDIATOOLS_GUI=1.

    Args:
        monkeypatch: Patcher.
    """
    monkeypatch.delenv(Prompt.GUI_ENV, raising=False)
    runner = JobRunner([sys.executable, "-c",
                        f"import os; print(os.environ.get('{Prompt.GUI_ENV}'))"])
    runner.start([])
    assert runner.wait(20) == 0
    assert runner.poll()[0].strip() == "1"


def test_console_hides_announcements_and_opens_dialogs(root: tk.Tk) -> None:
    """Announcement lines are hidden (also when split), dialogs answer the command.

    Args:
        root: Main window (fixture).
    """
    runner = JobRunner([sys.executable, "-c", "print(input('a? '))"])
    console = ConsoleView(root, runner, poll_ms=10)
    asked: list[dict] = []
    console.ask = asked.append
    spec = {"question": "Q?", "choices": [["p", "play"], ["", "go"]], "default": "p"}
    line = Prompt.MARKER + json.dumps(spec) + "\n"
    console.write("before\n" + line[:10])
    console.write(line[10:] + "@")
    console.write("@x\n" + Prompt.MARKER + "{broken\n")
    text = console.text.get("1.0", "end")
    assert asked == [spec] and "before" in text and "@@x" in text and "{broken" in text
    assert Prompt.MARKER.strip() not in text.replace(Prompt.MARKER + "{broken", "")
    console.answer_choice("p")
    assert "no command is running" in console.state.get()
    console.ask = console.open_dialog
    console.run([], "demo")
    console.write(line)
    assert console.dialog is not None
    console.dialog.choose("p")
    deadline = time.monotonic() + 20
    while "finished" not in console.state.get() and time.monotonic() < deadline:
        root.update()
        time.sleep(0.01)
    assert "a? p" in console.text.get("1.0", "end")
    console.write(line)
    console.send()                                    # closes the open dialog
    assert console.dialog is None


def test_choice_dialog_keys_and_close(root: tk.Tk) -> None:
    """Buttons, keys and Enter answer; closing sends nothing.

    Args:
        root: Main window (fixture).
    """
    sent: list[str] = []
    spec = {"question": "", "choices": [["p", "play"], ["k", "keep"]], "default": "k"}
    dialog = ChoiceDialog(root, spec, sent.append)
    dialog.buttons["p"].invoke()
    dialog = ChoiceDialog(root, spec, sent.append)
    dialog.on_key("k")(None)                                         # type: ignore[arg-type]
    dialog = ChoiceDialog(root, {"choices": [["", "continue"]]}, sent.append)
    dialog.close()
    dialog.close()
    assert sent == ["p", "k"]
