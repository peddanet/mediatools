"""Tests for tools/privacy_scan.py."""
import importlib.util
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from conftest import CHILD_TIMEOUT, make_file

SPEC = importlib.util.spec_from_file_location(
    "privacy_scan", Path(__file__).resolve().parent.parent / "tools" / "privacy_scan.py")
assert SPEC and SPEC.loader
privacy_scan = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(privacy_scan)

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


NO_REPLY = "1+t@users.noreply.github.com"


def git_repo(path: Path, message: str, email: str = NO_REPLY) -> None:
    """Create a repository with one commit of all files below ``path``.

    Args:
        path: Repository root (files must exist).
        message: Commit message.
        email: Address of author and committer.
    """
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": email,
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": email}
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", message]):
        subprocess.run(["git", "-C", str(path), *args], check=True, env=env, timeout=CHILD_TIMEOUT)


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
def test_clean_and_without_terms(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                 capsys: pytest.CaptureFixture) -> None:
    """A clean repository passes; without terms the scan fails, in CI it is skipped.

    Args:
        tmp_path: Temporary directory (fixture).
        monkeypatch: Environment patcher (fixture).
        capsys: Output capture (fixture).
    """
    make_file(tmp_path / "a.txt", b"nothing to see\n")
    git_repo(tmp_path, "initial")
    assert privacy_scan.PrivacyScan(str(tmp_path), ["hidden"]).run() == 0
    monkeypatch.delenv("CI", raising=False)
    assert privacy_scan.PrivacyScan(str(tmp_path), []).run() == 2
    assert "FAILED: no local terms" in capsys.readouterr().err
    monkeypatch.setenv("CI", "true")
    assert privacy_scan.PrivacyScan(str(tmp_path), []).run() == 0
    assert "skipped in CI" in capsys.readouterr().out


@needs_git
def test_identities_need_no_reply_addresses(tmp_path: Path,
                                            capsys: pytest.CaptureFixture) -> None:
    """Author, committer and tagger: terms are found, private addresses refused (masked).

    Args:
        tmp_path: Temporary directory (fixture).
        capsys: Output capture (fixture).
    """
    make_file(tmp_path / "a.txt", b"nothing to see\n")
    git_repo(tmp_path, "initial", email="someone@private-mail.example")
    env = {**os.environ, "GIT_COMMITTER_NAME": "Hiddenname", "GIT_COMMITTER_EMAIL": NO_REPLY}
    subprocess.run(["git", "-C", str(tmp_path), "tag", "-a", "v1", "-m", "v1"], check=True,
                   env=env, timeout=CHILD_TIMEOUT)
    subprocess.run(["git", "-C", str(tmp_path), "tag", "light"], check=True, env=env,
                   timeout=CHILD_TIMEOUT)
    scan = privacy_scan.PrivacyScan(str(tmp_path), ["hiddenname"])
    assert scan.run() == 1
    found = dict(scan.findings)
    assert found["commit " + scan.findings[0][0].split()[1] + " author"] \
        == "not a no-reply address: s***@p***.example"
    assert any(loc.startswith("tag v1 tagger name") for loc, _t in scan.findings)
    assert not any("light" in loc for loc, _t in scan.findings)     # no tagger
    assert "someone@" not in capsys.readouterr().out
    allowed = privacy_scan.PrivacyScan(str(tmp_path), ["unrelated"],
                                       ["someone@private-mail.example"])
    assert allowed.run() == 0
    assert privacy_scan.PrivacyScan.mask("x@localhost") == "x***@***"


@needs_git
def test_no_reply_login_is_not_a_finding(tmp_path: Path) -> None:
    """The login in a no-reply identity may be a deny-list term; another name is searched.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    make_file(tmp_path / "a.txt", b"nothing to see\n")
    git_repo(tmp_path, "initial")                         # name t, address 1+t@...
    assert privacy_scan.PrivacyScan(str(tmp_path), ["ttt", "users"]).run() == 0
    env = {**os.environ, "GIT_AUTHOR_NAME": "Hiddenname", "GIT_AUTHOR_EMAIL": NO_REPLY,
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": NO_REPLY}
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "--allow-empty", "-m", "x"],
                   check=True, env=env, timeout=CHILD_TIMEOUT)
    scan = privacy_scan.PrivacyScan(str(tmp_path), ["hiddenname"])
    assert scan.run() == 1 and scan.findings[0][0].endswith("author name:1")


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
    os.makedirs(tmp_path / "coll/_broken")
    (datadir / "mediatools.ini").write_text(f"[collection]\nroot = {tmp_path / 'coll'}\n",
                                            encoding="utf-8")
    terms = privacy_scan.PrivacyScan.local_terms()
    assert {"alpha", "beta", "UnitOne"} <= set(terms)
    assert "_broken" not in terms                  # created by mediatools, names no content


@needs_git
def test_allowed_phrases_are_blanked(tmp_path: Path) -> None:
    """A term inside an allowed phrase passes; elsewhere it is still found.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    make_file(tmp_path / "a.md", b"the sea level rises\nraise the level\nlevel=3\n")
    git_repo(tmp_path, "initial")
    scan = privacy_scan.PrivacyScan(str(tmp_path), ["level"], ["Sea Level", "level="])
    assert scan.run() == 1
    assert [location for location, _term in scan.findings] == ["a.md:2"]
    assert scan.allowed_count == 2


def test_read_list_ignores_comments(datadir: Path) -> None:
    """Comments and empty lines are skipped; a missing file is empty.

    Args:
        datadir: Test data directory (fixture, ``MEDIATOOLS_HOME``).
    """
    os.makedirs(datadir, exist_ok=True)
    (datadir / "privacy_allowlist.txt").write_text("# allowed\nsea level  # why\n\n",
                                                   encoding="utf-8")
    assert privacy_scan.PrivacyScan.read_list("privacy_allowlist.txt") == ["sea level"]
    assert privacy_scan.PrivacyScan.read_list("missing.txt") == []


def fake_tool(folder: Path, name: str, script: str) -> str:
    """Write an executable stand-in for a tool.

    Args:
        folder: Folder for the script.
        name: Program name.
        script: Shell commands after the shebang.

    Returns:
        Path of the script.
    """
    path = folder / name
    path.write_text("#!/bin/sh\n" + script + "\n", encoding="utf-8")
    path.chmod(0o755)
    return str(path)


def which_with(tools: dict[str, str | None]) -> Callable[[str], str | None]:
    """Return a ``shutil.which`` that knows the given tools and finds the rest normally.

    Args:
        tools: Program name -> path, or None for "not installed".

    Returns:
        Replacement for ``shutil.which``.
    """
    real = shutil.which
    return lambda name: tools[name] if name in tools else real(name)


@needs_git
def test_text_in_images_is_searched_and_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                               capsys: pytest.CaptureFixture) -> None:
    """A term shown in a picture is a finding; every picture is recognised once.

    Args:
        tmp_path: Temporary directory (fixture).
        monkeypatch: Replaces tesseract (fixture).
        capsys: Output capture (fixture).
    """
    repo, tools = tmp_path / "repo", tmp_path / "bin"
    tools.mkdir()
    make_file(repo / "docs/img/shot.png", b"\x89PNG\r\n\x1a\n\0fake image")
    git_repo(repo, "add a picture")
    calls = tmp_path / "calls"
    tesseract = fake_tool(tools, "tesseract", f'echo x >> "{calls}"\necho "Window: Secretname folder"')
    monkeypatch.setattr(privacy_scan.shutil, "which", which_with({"tesseract": tesseract}))
    scan = privacy_scan.PrivacyScan(str(repo), ["secretname"])
    assert scan.run() == 1
    assert ("docs/img/shot.png:1", "Secretname") in scan.findings
    assert "image text: 1 image(s) recognised, 0 from cache, 0 checked" in capsys.readouterr().out
    again = privacy_scan.PrivacyScan(str(repo), ["secretname"])
    assert again.run() == 1 and again.images == [0, 1, 0]
    assert calls.read_text(encoding="utf-8").count("x") == 1        # recognised once


@needs_git
def test_unreadable_image_is_a_finding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An image tesseract cannot read is not passed over silently.

    Args:
        tmp_path: Temporary directory (fixture).
        monkeypatch: Replaces tesseract (fixture).
    """
    repo, tools = tmp_path / "repo", tmp_path / "bin"
    tools.mkdir()
    make_file(repo / "logo.jpg", b"\xff\xd8\0not really")
    git_repo(repo, "add a logo")
    tesseract = fake_tool(tools, "tesseract", "echo broken >&2\nexit 1")
    monkeypatch.setattr(privacy_scan.shutil, "which", which_with({"tesseract": tesseract}))
    scan = privacy_scan.PrivacyScan(str(repo), ["secretname"])
    assert scan.run() == 1
    assert scan.findings == [("logo.jpg", "image text not readable by tesseract")]


@needs_git
@pytest.mark.parametrize(("name", "package"), [("tesseract", "tesseract-ocr"),
                                               ("pdftotext", "poppler-utils")])
def test_missing_reader_fails_the_scan(name: str, package: str, tmp_path: Path,
                                       monkeypatch: pytest.MonkeyPatch,
                                       capsys: pytest.CaptureFixture) -> None:
    """Without pdftotext or tesseract the scan fails instead of skipping files.

    Args:
        name: The missing tool.
        package: The package that provides it.
        tmp_path: Temporary directory (fixture).
        monkeypatch: Hides the tool (fixture).
        capsys: Output capture (fixture).
    """
    make_file(tmp_path / "docs/guide.pdf", b"%PDF-1.4 fake")
    make_file(tmp_path / "docs/img/shot.png", b"\x89PNG\r\n\x1a\n\0fake image")
    git_repo(tmp_path, "add documents")
    monkeypatch.setattr(privacy_scan.shutil, "which", which_with({name: None}))
    scan = privacy_scan.PrivacyScan(str(tmp_path), ["secretname"])
    assert scan.run() == 2
    err = capsys.readouterr().err
    assert f"{name} is missing" in err and f"sudo apt install {package}" in err


@needs_git
def test_image_with_text_source_is_checked_through_the_source(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A diagram rendered from a tracked .mmd or .svg is checked through that text, not by OCR.

    Args:
        tmp_path: Temporary directory (fixture).
        monkeypatch: Replaces tesseract (fixture).
    """
    repo, tools = tmp_path / "repo", tmp_path / "bin"
    tools.mkdir()
    make_file(repo / "docs/img/uml.mmd", b"classDiagram\n  class Store\n")
    make_file(repo / "docs/img/uml.png", b"\x89PNG\r\n\x1a\n\0rendered")
    make_file(repo / "docs/img/shot.png", b"\x89PNG\r\n\x1a\n\0screenshot")
    git_repo(repo, "add pictures")
    calls = tmp_path / "calls"
    tesseract = fake_tool(tools, "tesseract", f'echo "$1" >> "{calls}"\necho "Secretname Store"')
    monkeypatch.setattr(privacy_scan.shutil, "which", which_with({"tesseract": tesseract}))
    scan = privacy_scan.PrivacyScan(str(repo), ["secretname"])
    assert scan.run() == 1                                   # the screenshot's misreading counts
    assert [loc for loc, _ in scan.findings] == ["docs/img/shot.png:1"]
    assert scan.images == [1, 0, 1]
    assert "uml.png" not in calls.read_text(encoding="utf-8")
    assert scan.text_source("docs/img/uml.png") == "docs/img/uml.mmd"
    assert scan.text_source("docs/img/uml.mmd") is None
    assert scan.text_source("docs/img/shot.png") is None
