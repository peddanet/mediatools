"""End-to-end tests of the command line interface."""
import os
from pathlib import Path

import pytest

from conftest import make_file, needs_rsync
from mediatools.cli import Cli


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
