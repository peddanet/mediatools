"""End-to-end tests of the command line interface."""
import os
import runpy
import sys
from pathlib import Path

import pytest

from conftest import make_file, needs_rsync
from mediatools.cli import Cli
from mediatools.core.versioninfo import VersionInfo


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
