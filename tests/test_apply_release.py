"""mediatools update (apply-release.sh): choice of patch archives, tidying of downloads.

The git and GitHub steps are not run here (they need GitHub); these tests
cover what the script decides from the download folder and the released
version, using a local clone with a bare repository as ``origin``.
"""
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from mediatools.cli import Cli
from mediatools.update import ReleaseUpdater

SCRIPT = (Path(__file__).resolve().parent.parent / "src" / "mediatools" / "update"
          / "apply-release.sh")


def git(*args: str, cwd: Path) -> None:
    """Run git quietly.

    Args:
        *args: git arguments.
        cwd: Working directory.
    """
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture(name="setup")
def fixture_setup(tmp_path: Path) -> dict[str, Path]:
    """A clone whose main is at 1.2.5, and an empty download folder.

    Args:
        tmp_path: Temporary directory (fixture).

    Returns:
        Paths ``home``, ``repo`` and ``downloads``.
    """
    home, repo, downloads = tmp_path / "home", tmp_path / "repo", tmp_path / "home" / "Downloads"
    downloads.mkdir(parents=True)
    git("init", "-q", "--bare", str(tmp_path / "origin.git"), cwd=tmp_path)
    git("clone", "-q", str(tmp_path / "origin.git"), str(repo), cwd=tmp_path)
    init = repo / "src" / "mediatools" / "__init__.py"
    init.parent.mkdir(parents=True)
    init.write_text('__version__ = "1.2.5"\n', encoding="utf-8")
    git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "checkout", "-q", "-b",
        "main", cwd=repo)
    git("add", "-A", cwd=repo)
    git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "x",
        cwd=repo)
    git("push", "-q", "origin", "main", cwd=repo)
    return {"home": home, "repo": repo, "downloads": downloads}


def touch(path: Path, age: int) -> Path:
    """Create a file with a modification time ``age`` seconds in the past.

    Args:
        path: File to create.
        age: Age in seconds.

    Returns:
        The path.
    """
    path.write_text(path.name, encoding="utf-8")
    stamp = time.time() - age
    os.utime(path, (stamp, stamp))
    return path


def run(setup: dict[str, Path], *args: str) -> str:
    """Run the script with HOME pointing into the test directory.

    Args:
        setup: Paths from the fixture.
        *args: Script arguments.

    Returns:
        Combined output.
    """
    env = dict(os.environ, HOME=str(setup["home"]), APPLY_RELEASE_ALLOW_ROOT="1")
    env.pop("APPLY_RELEASE_ORIGIN", None)
    result = subprocess.run(["bash", str(SCRIPT), "--repo", str(setup["repo"]), *args],
                            env=env, capture_output=True, text=True, check=True, timeout=60)
    return result.stdout + result.stderr


def test_list_picks_newest_copy_and_marks_stale(setup: dict[str, Path]) -> None:
    """Per version the newest copy is used; released versions and older copies are stale.

    Args:
        setup: Paths from the fixture.
    """
    dl = setup["downloads"]
    touch(dl / "mediatools-hotfix-1.2.6-patch.tar.gz", 300)
    touch(dl / "mediatools-hotfix-1.2.6-patch (1).tar.gz", 100)
    touch(dl / "mediatools-hotfix-1.2.10-patch.tar.gz", 50)
    touch(dl / "mediatools-hotfix-1.2.5-patch.tar.gz", 50)
    touch(dl / "unrelated.tar.gz", 10)
    out = run(setup, "--list")
    assert "released on main: 1.2.5" in out
    pending = [line.split(None, 2)[1:] for line in out.splitlines()
               if line.strip().startswith("pending")]
    assert pending == [["1.2.6", "mediatools-hotfix-1.2.6-patch (1).tar.gz"],
                       ["1.2.10", "mediatools-hotfix-1.2.10-patch.tar.gz"]]
    stale = sorted(line.split(None, 2)[2] for line in out.splitlines()
                   if line.strip().startswith("stale"))
    assert stale == ["mediatools-hotfix-1.2.5-patch.tar.gz",
                     "mediatools-hotfix-1.2.6-patch.tar.gz"]
    assert len(list(dl.iterdir())) == 5                 # --list changes nothing


def test_nothing_pending_tidies_the_folder(setup: dict[str, Path]) -> None:
    """Without pending patches stale archives and old apply scripts are removed.

    Args:
        setup: Paths from the fixture.
    """
    dl = setup["downloads"]
    touch(dl / "mediatools-hotfix-1.2.4-patch.tar.gz", 300)
    touch(dl / "mediatools-hotfix-1.2.5-patch (1).tar.gz", 300)
    touch(dl / "apply-mediatools-1.2.5.sh", 300)
    touch(dl / "apply-mediatools-1.2.6 (1).sh", 300)
    touch(dl / "holiday.jpg", 300)
    out = run(setup, "-y")
    assert "pending patches:  none" in out and "nothing applied" in out
    assert sorted(p.name for p in dl.iterdir()) == ["holiday.jpg"]
    logs = list((setup["home"] / "scripts" / "mediatools.d" / "logs").iterdir())
    assert len(logs) == 1 and logs[0].name.startswith("apply-release_")


def test_help_and_unknown_option(setup: dict[str, Path]) -> None:
    """--help prints the header; an unknown option is refused.

    Args:
        setup: Paths from the fixture.
    """
    assert "apply mediatools release patches" in run(setup, "--help")
    env = dict(os.environ, HOME=str(setup["home"]))
    result = subprocess.run(["bash", str(SCRIPT), "--bogus"], env=env, capture_output=True,
                            text=True, check=False)
    assert result.returncode == 2 and "unknown option" in result.stdout


def test_inherited_variables_never_delete_the_script(setup: dict[str, Path],
                                                     tmp_path: Path) -> None:
    """Variables leaked from an outer run (pre-push hook) must not delete the original.

    Regression: the outer run exported its control variables; the tests in
    the hook ran the script again, which took itself for the temporary copy
    and removed itself on exit.

    Args:
        setup: Paths from the fixture.
        tmp_path: Temporary directory (fixture).
    """
    script = tmp_path / "apply-release.sh"
    script.write_bytes(SCRIPT.read_bytes())
    env = dict(os.environ, HOME=str(setup["home"]), APPLY_RELEASE_ALLOW_ROOT="1",
               APPLY_RELEASE_ORIGIN=str(script), APPLY_RELEASE_COPY="/tmp/other-copy.sh")
    for args in (["--bogus"], ["--repo", str(setup["repo"]), "--list"]):
        subprocess.run(["bash", str(script), *args], env=env, capture_output=True,
                       text=True, check=False, timeout=60)
        assert script.is_file()


def test_control_variables_are_not_exported(setup: dict[str, Path], tmp_path: Path) -> None:
    """Programs started by the script (git and its hooks) do not see its variables.

    A stand-in ``git`` early in PATH records the two variables and then
    runs the real git.

    Args:
        setup: Paths from the fixture.
        tmp_path: Temporary directory (fixture).
    """
    real_git = shutil.which("git")
    record = tmp_path / "seen.txt"
    fake = tmp_path / "bin" / "git"
    fake.parent.mkdir()
    fake.write_text(f'#!/bin/sh\necho "[$APPLY_RELEASE_ORIGIN][$APPLY_RELEASE_COPY]" >> {record}\n'
                    f'exec {real_git} "$@"\n', encoding="utf-8")
    fake.chmod(0o755)
    env = dict(os.environ, HOME=str(setup["home"]), APPLY_RELEASE_ALLOW_ROOT="1",
               PATH=f"{fake.parent}{os.pathsep}{os.environ['PATH']}")
    env.pop("APPLY_RELEASE_ORIGIN", None)
    env.pop("APPLY_RELEASE_COPY", None)
    subprocess.run(["bash", str(SCRIPT), "--repo", str(setup["repo"]), "--list"],
                   env=env, capture_output=True, text=True, check=True, timeout=60)
    seen = set(record.read_text(encoding="utf-8").splitlines())
    assert seen == {"[][]"}


def test_update_subcommand_forwards_options(setup: dict[str, Path],
                                            monkeypatch: pytest.MonkeyPatch,
                                            capfd: pytest.CaptureFixture) -> None:
    """``mediatools update --list`` runs the bundled script with the options.

    Args:
        setup: Paths from the fixture.
        monkeypatch: Patch helper (fixture).
        capfd: Output capture on file descriptor level (fixture).
    """
    monkeypatch.setenv("HOME", str(setup["home"]))
    monkeypatch.setenv("APPLY_RELEASE_ALLOW_ROOT", "1")
    monkeypatch.delenv("APPLY_RELEASE_ORIGIN", raising=False)
    assert Cli().main(["update", "--list", "--repo", str(setup["repo"]),
                       "--downloads", str(setup["downloads"])]) == 0
    assert "released on main: 1.2.5" in capfd.readouterr().out
    updater = ReleaseUpdater(["-y"])
    assert updater.command()[1:] == [str(SCRIPT), "-y"]
    updater.script = setup["home"] / "missing.sh"
    with pytest.raises(FileNotFoundError):
        updater.command()
