"""check.sh checks each tree in full once (dev-conventions templates/check-cache.sh)."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import CHILD_TIMEOUT

ROOT = Path(__file__).resolve().parent.parent
STUBS = ("pycodestyle", "pylint", "mypy", "bandit", "shellcheck",
         "ffmpeg", "ffprobe", "rsync", "rsnapshot")
FAKE_PYTHON = """#!/bin/sh
# records the gates; 'import tkinter' and the key computation use the real Python
case "$1 $2" in
  "-m pytest") echo tests >> "$CALLS"; exit 0 ;;
  "tools/privacy_scan.py "*) echo privacy >> "$CALLS"; exit 0 ;;
esac
exec "$REAL_PYTHON" "$@"
"""


@pytest.fixture(name="project")
def fixture_project(tmp_path: Path) -> dict[str, Path]:
    """A git project with check.sh and stand-ins for every tool it calls.

    Args:
        tmp_path: Temporary directory (fixture).

    Returns:
        Paths ``project``, ``bin`` and ``calls``.
    """
    project, stubs, calls = tmp_path / "project", tmp_path / "bin", tmp_path / "calls.txt"
    (project / "tools").mkdir(parents=True)
    shutil.copy2(ROOT / "tools" / "check.sh", project / "tools" / "check.sh")
    shutil.copy2(ROOT / "tools" / "output.sh", project / "tools" / "output.sh")
    (project / "tools" / "privacy_scan.py").write_text("", encoding="utf-8")
    (project / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    stubs.mkdir()
    for name in STUBS:
        stub = stubs / name
        stub.write_text(f'#!/bin/sh\necho {name} >> "$CALLS"\n', encoding="utf-8")
        stub.chmod(0o755)
    python = stubs / "python3"
    python.write_text(FAKE_PYTHON, encoding="utf-8")
    python.chmod(0o755)
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.name=t", "-c", "user.email=1+t@users.noreply.github.com",
                  "commit", "-q", "-m", "base"]):
        subprocess.run(["git", *args], cwd=project, check=True, capture_output=True,
                       timeout=CHILD_TIMEOUT)
    return {"project": project, "bin": stubs, "calls": calls}


def check(world: dict[str, Path], **extra: str) -> tuple[str, list[str]]:
    """Run check.sh once and return its output and the gates it called.

    Args:
        world: Paths from the fixture.
        **extra: Additional environment variables.

    Returns:
        Standard output and the list of called gate tools.
    """
    world["calls"].unlink(missing_ok=True)
    env = {"PATH": f"{world['bin']}{os.pathsep}/usr/bin:/bin", "HOME": str(world["project"]),
           "CALLS": str(world["calls"]), "REAL_PYTHON": sys.executable,
           "MEDIATOOLS_XVFB": "1", "VIRTUAL_ENV": "outside", **extra}
    res = subprocess.run(["bash", "tools/check.sh"], cwd=world["project"], env=env,
                         capture_output=True, text=True, check=False, timeout=CHILD_TIMEOUT)
    assert res.returncode == 0, res.stdout + res.stderr
    called = world["calls"].read_text(encoding="utf-8").split() if world["calls"].exists() \
        else []
    return res.stdout, called


def test_second_run_of_the_same_tree_runs_only_privacy_and_shell(
        project: dict[str, Path]) -> None:
    """Full once; then privacy and shell only; CHECK_FULL, CI or a change: full again.

    Args:
        project: Paths from the fixture.
    """
    _out, called = check(project)
    assert "tests" in called and "pylint" in called
    assert (project["project"] / ".git" / "checked-trees").is_file()
    out, called = check(project)
    assert called == ["privacy", "shellcheck"]
    assert "passed all gates before" in out and "CHECK_FULL=1" in out
    assert "tests" in check(project, CHECK_FULL="1")[1]
    assert "tests" in check(project, CI="true")[1]
    (project["project"] / "new.txt").write_text("changed\n", encoding="utf-8")
    assert "tests" in check(project)[1]


def test_without_git_nothing_is_cached(project: dict[str, Path]) -> None:
    """An unpacked archive (no .git) is checked in full every time.

    Args:
        project: Paths from the fixture.
    """
    shutil.rmtree(project["project"] / ".git")
    for _run in range(2):
        assert "tests" in check(project)[1]
    assert not (project["project"] / ".git").exists()
