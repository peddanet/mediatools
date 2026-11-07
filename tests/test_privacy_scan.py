"""Tests for tools/privacy_scan.py."""
import importlib.util
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import make_file

SPEC = importlib.util.spec_from_file_location(
    "privacy_scan", Path(__file__).resolve().parent.parent / "tools" / "privacy_scan.py")
assert SPEC and SPEC.loader
privacy_scan = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(privacy_scan)

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def git_repo(path: Path, message: str) -> None:
    """Create a repository with one commit of all files below ``path``.

    Args:
        path: Repository root (files must exist).
        message: Commit message.
    """
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", message]):
        subprocess.run(["git", "-C", str(path), *args], check=True, env=env)


@needs_git
def test_finds_terms_in_files_paths_and_messages(tmp_path: Path,
                                                 capsys: pytest.CaptureFixture) -> None:
    """Terms are found in content, file names and commit messages.

    Args:
        tmp_path: Temporary directory (fixture).
        capsys: Output capture (fixture).
    """
    make_file(tmp_path / "docs/a.md", b"An example with Secretname inside.\n")
    make_file(tmp_path / "secretname_notes.txt", b"neutral\n")
    make_file(tmp_path / "words.txt", b"concatenate the files\n")
    git_repo(tmp_path, "add notes about secretname")
    scan = privacy_scan.PrivacyScan(str(tmp_path), ["secretname", "cat", "ab"])
    assert scan.run() == 1
    locations = [location for location, _term in scan.findings]
    assert "docs/a.md:1" in locations
    assert any(loc.startswith("path secretname_notes.txt") for loc in locations)
    assert any(loc.startswith("commit messages") for loc in locations)
    assert not any("words.txt" in loc for loc in locations)      # no partial words
    assert "ab" not in scan.terms                                # too short
    assert "finding(s)" in capsys.readouterr().out


@needs_git
def test_clean_and_skipped(tmp_path: Path) -> None:
    """A clean repository passes; without terms the scan is skipped.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    make_file(tmp_path / "a.txt", b"nothing to see\n")
    git_repo(tmp_path, "initial")
    assert privacy_scan.PrivacyScan(str(tmp_path), ["hidden"]).run() == 0
    assert privacy_scan.PrivacyScan(str(tmp_path), []).run() == 0


def test_local_terms_from_denylist_and_collection(datadir: Path, tmp_path: Path) -> None:
    """Terms come from the deny list and the collection's unit names.

    Args:
        datadir: Test data directory (fixture, ``MEDIATOOLS_HOME``).
        tmp_path: Temporary directory (fixture).
    """
    os.makedirs(datadir, exist_ok=True)
    (datadir / "privacy_denylist.txt").write_text("alpha # comment\n\nbeta\n",
                                                  encoding="utf-8")
    os.makedirs(tmp_path / "coll/UnitOne")
    (datadir / "mediatools.ini").write_text(f"[collection]\nroot = {tmp_path / 'coll'}\n",
                                            encoding="utf-8")
    terms = privacy_scan.PrivacyScan.local_terms()
    assert {"alpha", "beta", "UnitOne"} <= set(terms)
