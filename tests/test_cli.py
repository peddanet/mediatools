"""End-to-end tests of the command line interface."""
import os
import runpy
import shutil
import sys
from pathlib import Path

import pytest

from conftest import make_file, make_video, needs_ffmpeg, needs_rsync
from mediatools.cli import Cli
from mediatools.core.probe import MediaProbe
from mediatools.core.prompt import Prompt
from mediatools.core.versioninfo import VersionInfo
from mediatools.sync import TargetPicker


def test_help_and_version(capsys: pytest.CaptureFixture) -> None:
    """Without a subcommand the help is printed and 1 returned.

    Args:
        capsys: Output capture (fixture).
    """
    assert Cli().main([]) == 1
    assert "shrink" in capsys.readouterr().out


def test_flatten_dry_run_then_apply_then_undo(tmp_path: Path, datadir: Path) -> None:
    """Dry run changes nothing; apply creates links; undo removes them.

    Args:
        tmp_path: Temporary directory (fixture).
        datadir: Temporary data directory (fixture).
    """
    make_file(tmp_path / "u/posts/a.jpg")
    assert Cli().main(["flatten", str(tmp_path), "--dry-run"]) == 0
    assert not os.path.exists(tmp_path / "u/_flat")
    assert Cli().main(["flatten", str(tmp_path), "--yes"]) == 0
    assert os.path.exists(tmp_path / "u/_flat/posts__a.jpg")
    plans = sorted(os.listdir(datadir / "plans"))
    assert Cli().main(["flatten", "--undo", str(datadir / "plans" / plans[-1])]) == 0
    assert not os.path.exists(tmp_path / "u/_flat/posts__a.jpg")


def test_progress_cli(tmp_path: Path) -> None:
    """The progress command runs on a plain directory.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    make_file(tmp_path / "a.mp4")
    assert Cli().main(["progress", str(tmp_path)]) == 0


@needs_rsync
def test_sync_cli_and_reverse(tmp_path: Path) -> None:
    """Sync copies forward, --reverse copies back.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    src, dst = tmp_path / "src", tmp_path / "dst"
    make_file(src / "a.jpg", b"A")
    assert Cli().main(["sync", str(src), str(dst), "--yes"]) == 0
    assert os.path.exists(dst / "a.jpg")
    make_file(dst / "b.jpg", b"B")
    assert Cli().main(["sync", str(src), str(dst), "--reverse", "--yes", "--no-report"]) == 0
    assert os.path.exists(src / "b.jpg")


def test_version_installed_and_checkout(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """--version names a source checkout; an installed package shows the number only.

    Args:
        tmp_path: Temporary directory (fixture).
        capsys: Output capture (fixture).
    """
    clone = tmp_path / "clone"
    make_file(clone / "pyproject.toml")
    os.makedirs(clone / "src/mediatools")
    assert VersionInfo(str(clone / "src/mediatools"), "9.9.9").describe() == \
        f"9.9.9 (source checkout {clone}, not an installed release)"
    site = tmp_path / "venv/lib/python3.12/site-packages/mediatools"
    os.makedirs(site)
    assert VersionInfo(str(site), "9.9.9").describe() == "9.9.9"
    assert Cli().main(["--version"]) == 0
    assert capsys.readouterr().out.startswith("mediatools ")


def test_module_entry_point(monkeypatch: pytest.MonkeyPatch) -> None:
    """``python3 -m mediatools`` exits with the code of the command.

    Args:
        monkeypatch: Patcher (fixture).
    """
    monkeypatch.setattr(sys, "argv", ["mediatools", "--version"])
    with pytest.raises(SystemExit) as stop:
        runpy.run_module("mediatools", run_name="__main__")
    assert stop.value.code == 0


def test_argument_errors(tmp_path: Path) -> None:
    """Unknown arguments, missing directories and tools stop with a message.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    with pytest.raises(SystemExit):
        Cli().main(["progress", str(tmp_path), "one", "two"])
    with pytest.raises(SystemExit, match="not a directory"):
        Cli().main(["progress", str(tmp_path / "missing")])
    with pytest.raises(SystemExit, match="not found: no-such-tool-x"):
        Cli._need("no-such-tool-x")  # pylint: disable=protected-access
    with pytest.raises(SystemExit, match="larger than"):
        Cli().main(["triage", str(tmp_path), "--min-seconds", "9", "--max-seconds", "1"])


def test_config_path_render_and_bad_set(datadir: Path,
                                        capsys: pytest.CaptureFixture) -> None:
    """config --path, plain config and an invalid --set item.

    Args:
        datadir: Test data directory (fixture).
        capsys: Output capture (fixture).
    """
    assert Cli().main(["config", "--path"]) == 0
    assert capsys.readouterr().out.strip() == str(datadir / "mediatools.ini")
    assert Cli().main(["config"]) == 0
    assert "file missing" in capsys.readouterr().out
    assert Cli().main(["config", "--init"]) == 0
    assert Cli().main(["config", "--init"]) == 0
    assert "already exists" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="SECTION.KEY=VALUE"):
        Cli().main(["config", "--set", "nodot=1"])


def test_rename_db_dry_run_keeps_files(tmp_path: Path) -> None:
    """rename-db --dry-run renames nothing; a following run does.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    make_file(tmp_path / "u/old.db")
    make_file(tmp_path / "v/old.db")
    make_file(tmp_path / "v/new.db")
    assert Cli().main(["rename-db", "old.db", "new.db", str(tmp_path), "--dry-run"]) == 0
    assert os.path.exists(tmp_path / "u/old.db")
    assert Cli().main(["rename-db", "old.db", "new.db", str(tmp_path)]) == 0
    assert os.path.exists(tmp_path / "u/new.db")
    assert os.path.exists(tmp_path / "v/old.db")


def test_triage_dry_run_lists_candidates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """triage --dry-run counts and lists without opening a player.

    Args:
        tmp_path: Temporary directory (fixture).
        monkeypatch: Patcher (fixture).
    """
    make_file(tmp_path / "u/a.mp4")
    monkeypatch.setattr(shutil, "which", lambda tool: "/usr/bin/" + tool)
    monkeypatch.setattr(MediaProbe, "duration", lambda self, path: 5.0)
    assert Cli().main(["triage", str(tmp_path), "--dry-run"]) == 0


def test_sync_report_only_and_aborted_choice(tmp_path: Path,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    """--report-only transfers nothing; an aborted disk choice returns 1.

    Args:
        tmp_path: Temporary directory (fixture).
        monkeypatch: Patcher (fixture).
    """
    src, dst = tmp_path / "src", tmp_path / "dst"
    make_file(src / "a.jpg")
    make_file(dst / "only_here.jpg")
    assert Cli().main(["sync", str(src), str(dst), "--report-only"]) == 0
    assert not os.path.exists(dst / "a.jpg")
    monkeypatch.setattr(TargetPicker, "pick", lambda self, source: None)
    assert Cli().main(["sync", str(src)]) == 1


def test_snapshot_adopt_needs_directory(datadir: Path, tmp_path: Path) -> None:
    """snapshot adopt without the existing copy explains the call.

    Args:
        datadir: Test data directory (fixture).
        tmp_path: Temporary directory (fixture).
    """
    os.makedirs(datadir, exist_ok=True)
    (datadir / "mediatools.ini").write_text(
        f"[snapshot]\nsource = {tmp_path}\ndisk = {tmp_path}\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="adopt needs"):
        Cli().main(["snapshot", "adopt"])


def test_triage_start_declined(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Declining the start question opens no player and removes nothing.

    Args:
        tmp_path: Temporary directory (fixture).
        monkeypatch: Patcher (fixture).
    """
    clip = make_file(tmp_path / "u/a.mp4")
    monkeypatch.setattr(shutil, "which", lambda tool: "/usr/bin/" + tool)
    monkeypatch.setattr(MediaProbe, "duration", lambda self, path: 5.0)
    monkeypatch.setattr(Prompt, "yes_no", lambda self, question, default=False: False)
    assert Cli().main(["triage", str(tmp_path)]) == 0
    assert os.path.getsize(clip) == 1


@needs_ffmpeg
def test_shrink_keep_copies_via_cli(tmp_path: Path) -> None:
    """shrink --keep-copies -y leaves the original and writes the copy.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    original = make_video(tmp_path / "u/a.mp4", seconds=1)
    size = os.path.getsize(original)
    assert Cli().main(["shrink", str(tmp_path), "--keep-copies", "-y", "--threads", "1"]) == 0
    assert os.path.getsize(original) == size
    assert os.path.exists(tmp_path / "u/newShrinkedFiles/a.mp4")
