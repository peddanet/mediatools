"""Tests of install.sh: release selection from tags (no network, no pip)."""
import os
import subprocess
from pathlib import Path

import pytest

INSTALL = str(Path(__file__).resolve().parents[1] / "install.sh")


def git(*args: str, cwd: Path) -> None:
    """Run git with a fixed identity.

    Args:
        *args: git arguments.
        cwd: Working directory.
    """
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                   cwd=cwd, check=True, capture_output=True)


def run_install(origin: Path, home: Path, *args: str) -> subprocess.CompletedProcess:
    """Run install.sh against a local repository.

    Args:
        origin: Repository that stands in for GitHub.
        home: Home directory for the run.
        *args: Arguments of install.sh.

    Returns:
        The finished process.
    """
    env = dict(os.environ, HOME=str(home), MEDIATOOLS_REPO=f"file://{origin}",
               MEDIATOOLS_HOME=str(home / "data"), BIN_DIR=str(home / "bin"))
    return subprocess.run(["bash", INSTALL, *args], env=env, capture_output=True, text=True,
                          check=False)


@pytest.fixture(name="repo")
def repo_fixture(tmp_path: Path) -> Path:
    """Create a repository with release and non-release tags.

    Args:
        tmp_path: Temporary directory (fixture).

    Returns:
        Path of the repository.
    """
    path = tmp_path / "repo"
    path.mkdir()
    git("init", "-q", cwd=path)
    (path / "f").write_text("x", encoding="utf-8")
    git("add", "f", cwd=path)
    git("commit", "-qm", "c", cwd=path)
    for tag in ("v1.2.0", "v1.10.0", "v1.9.3", "v2.0.0-rc1", "latest"):
        git("tag", tag, cwd=path)
    return path


def test_list_in_version_order(repo: Path, tmp_path: Path) -> None:
    """Only vX.Y.Z tags are releases; they are sorted as versions.

    Args:
        repo: Test repository (fixture).
        tmp_path: Temporary directory (fixture).
    """
    res = run_install(repo, tmp_path / "home", "--list")
    assert res.returncode == 0
    assert res.stdout.split() == ["v1.2.0", "v1.9.3", "v1.10.0"]


def test_unknown_release_and_option_refused(repo: Path, tmp_path: Path) -> None:
    """An unknown tag or option stops before anything is written.

    Args:
        repo: Test repository (fixture).
        tmp_path: Temporary directory (fixture).
    """
    home = tmp_path / "home"
    res = run_install(repo, home, "v9.9.9")
    assert res.returncode == 1 and "unknown release" in res.stderr
    res = run_install(repo, home, "--force")
    assert res.returncode == 1 and "unknown option" in res.stderr
    assert not os.path.exists(home / "data")


def test_foreign_file_is_never_replaced(repo: Path, tmp_path: Path) -> None:
    """A file in BIN_DIR that the installer did not create is left alone.

    Args:
        repo: Test repository (fixture).
        tmp_path: Temporary directory (fixture).
    """
    home = tmp_path / "home"
    (home / "bin").mkdir(parents=True)
    (home / "bin/mediatools").write_text("mine", encoding="utf-8")
    res = run_install(repo, home)
    assert res.returncode == 1 and "foreign file" in res.stderr
    assert (home / "bin/mediatools").read_text(encoding="utf-8") == "mine"
    assert not os.path.exists(home / "data/venv")


def test_repository_without_releases(tmp_path: Path) -> None:
    """A repository without release tags is reported.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    empty = tmp_path / "empty"
    empty.mkdir()
    git("init", "-q", cwd=empty)
    res = run_install(empty, tmp_path / "home")
    assert res.returncode == 1 and "no release tag" in res.stderr


def test_help_lists_usage(tmp_path: Path) -> None:
    """--help prints the usage block of the script header.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    res = run_install(tmp_path, tmp_path / "home", "--help")
    assert res.returncode == 0 and "./install.sh --list" in res.stdout
