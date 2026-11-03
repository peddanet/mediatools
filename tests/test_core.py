"""Unit tests for mediatools.core."""
import os
from pathlib import Path

from conftest import make_file
from mediatools.core import DataDir, MediaTypes, Prompt, TreeWalker


def test_media_types() -> None:
    """Extensions are classified case-insensitively and parsed from CSV."""
    assert MediaTypes.is_media("a.MP4")
    assert MediaTypes.is_video("x.mkv")
    assert not MediaTypes.is_media("shrink.db")
    assert MediaTypes.parse_extensions("mp4, .MOV,") == frozenset({".mp4", ".mov"})


def test_datadir_uses_env(datadir: Path) -> None:
    """The data root follows MEDIATOOLS_HOME.

    Args:
        datadir: Temporary data directory (fixture).
    """
    data = DataDir()
    assert data.root == str(datadir)
    path = data.file("logs", "x", ".log")
    assert os.path.dirname(path) == os.path.join(str(datadir), "logs")


def test_walker_skips_reserved_and_hidden(tmp_path: Path) -> None:
    """_flat, newShrinkedFiles, hidden dirs and symlinks are never yielded.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    for rel in ("a/v.mp4", "a/_flat/v.mp4", "a/newShrinkedFiles/v.mp4", "a/.hidden/v.mp4"):
        make_file(tmp_path / rel)
    os.symlink(tmp_path / "a/v.mp4", tmp_path / "a/link.mp4")
    found = [os.path.relpath(p, tmp_path) for p in TreeWalker().media_files(str(tmp_path))]
    assert found == ["a/v.mp4"]


def test_prompt_answers() -> None:
    """Invalid answers are repeated; empty input returns the default."""
    answers = iter(["maybe", "y"])
    assert Prompt(input_func=lambda _q: next(answers)).yes_no("?") is True
    assert Prompt(input_func=lambda _q: "").yes_no("?", default=False) is False
    assert Prompt(assume_yes=True).yes_no("?") is True
