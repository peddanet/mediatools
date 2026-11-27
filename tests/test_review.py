"""Tests of tools/review.sh (Beyond Compare review of two git refs)."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REVIEW = Path(__file__).resolve().parents[1] / "tools" / "review.sh"
FAKE_BCOMPARE = """#!/usr/bin/env bash
printf '%s\\n' "$@" > "$FAKE_BC_ARGS"
exit "${FAKE_BC_CODE:-0}"
"""


def run(cwd: Path, env: dict[str, str], *args: str,
        check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a command in a directory.

    Args:
        cwd: Working directory.
        env: Environment.
        *args: Command line.
        check: Raise on a non-zero exit code.

    Returns:
        The finished process with its output.
    """
    return subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True,
                          check=check, timeout=300)


@pytest.fixture(name="repo")
def fixture_repo(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """Provide a repository with main and feature/x, and a fake Beyond Compare.

    Args:
        tmp_path: Temporary directory (fixture).

    Returns:
        Repository path and environment.
    """
    home = tmp_path / "home"
    home.mkdir()
    fake = tmp_path / "bcompare"
    fake.write_text(FAKE_BCOMPARE, encoding="utf-8")
    fake.chmod(0o755)
    temp = tmp_path / "tmp"
    temp.mkdir()
    env = dict(os.environ)
    env.update(HOME=str(home), GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.org",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.org",
               GIT_CONFIG_NOSYSTEM="1", BCOMPARE=str(fake), TMPDIR=str(temp),
               FAKE_BC_ARGS=str(tmp_path / "bc-args"))
    work = tmp_path / "work"
    work.mkdir()
    run(work, env, "git", "init", "-q", "-b", "main")
    (work / "same.txt").write_text("same\n", encoding="utf-8")
    (work / "changed.txt").write_text("old\n", encoding="utf-8")
    run(work, env, "git", "add", "-A")
    run(work, env, "git", "commit", "-qm", "Base")
    run(work, env, "git", "switch", "-qc", "feature/x")
    (work / "changed.txt").write_text("new\n", encoding="utf-8")
    run(work, env, "git", "commit", "-qam", "Change it")
    return work, env


def short(work: Path, env: dict[str, str], ref: str) -> str:
    """Return the short commit hash of a ref.

    Args:
        work: Repository.
        env: Environment.
        ref: Branch, tag or commit.

    Returns:
        The short hash.
    """
    return run(work, env, "git", "rev-parse", "--short", ref).stdout.strip()


def test_real_copies_named_after_the_refs(repo: tuple[Path, dict[str, str]]) -> None:
    """Both sides are real files in folders named after ref and hash; titles name them.

    Args:
        repo: Prepared repository (fixture).
    """
    work, env = repo
    done = run(work, env, str(REVIEW), "main", "feature/x")
    args = Path(env["FAKE_BC_ARGS"]).read_text(encoding="utf-8").splitlines()
    left, right = Path(args[0]), Path(args[1])
    assert left.name == f"left_main@{short(work, env, 'main')}"
    assert right.name == f"right_feature-x@{short(work, env, 'feature/x')}"
    for side in (left, right):
        for item in side.rglob("*"):
            assert not item.is_symlink(), item
    assert (left / "changed.txt").read_text(encoding="utf-8") == "old\n"
    assert (right / "changed.txt").read_text(encoding="utf-8") == "new\n"
    assert (right / "same.txt").read_text(encoding="utf-8") == "same\n"
    assert args[2] == f"-lefttitle=left: main @ {short(work, env, 'main')}: Base"
    assert args[3] == (f"-righttitle=right: feature/x @ {short(work, env, 'feature/x')}"
                       ": Change it")
    assert "Opening Beyond Compare" in done.stdout
    assert "changed.txt" in done.stdout
    status = run(work, env, "git", "status", "--porcelain").stdout
    assert status == ""


def test_same_tree_opens_nothing(repo: tuple[Path, dict[str, str]]) -> None:
    """Two refs with the same tree are reported and Beyond Compare is not started.

    Args:
        repo: Prepared repository (fixture).
    """
    work, env = repo
    done = run(work, env, str(REVIEW), "feature/x", "HEAD")
    assert "No difference" in done.stdout
    assert not Path(env["FAKE_BC_ARGS"]).exists()


def test_comparison_codes_are_no_error(repo: tuple[Path, dict[str, str]]) -> None:
    """Return codes below 100 describe the comparison; 100 and above are errors.

    Args:
        repo: Prepared repository (fixture).
    """
    work, env = repo
    assert run(work, env | {"FAKE_BC_CODE": "13"}, str(REVIEW), "main", "HEAD")
    failed = run(work, env | {"FAKE_BC_CODE": "100"}, str(REVIEW), "main", "HEAD",
                 check=False)
    assert failed.returncode == 1
    assert "error code 100" in failed.stderr


def test_usage_errors(repo: tuple[Path, dict[str, str]]) -> None:
    """Wrong arguments, an unknown ref and a missing program stop with a reason.

    Args:
        repo: Prepared repository (fixture).
    """
    work, env = repo
    assert run(work, env, str(REVIEW), "main", check=False).returncode == 2
    unknown = run(work, env, str(REVIEW), "main", "nope", check=False)
    assert unknown.returncode == 2
    assert "unknown ref: nope" in unknown.stderr
    missing = run(work, env | {"BCOMPARE": "no-such-bcompare"}, str(REVIEW), "main",
                  "HEAD", check=False)
    assert missing.returncode == 1
    assert "not found" in missing.stderr
