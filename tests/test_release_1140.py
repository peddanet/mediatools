"""Release 1.14.0: XDG installation, cache folder, privacy refusal, venv in check.sh.

install.sh runs with a stand-in for the pip step (``PIP_INSTALL_CMD``) that
writes a program reporting the tag's version, so no network is needed.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import tkinter as tk

import pytest

from conftest import CHILD_TIMEOUT

from test_gui_logic import write_run
from test_release_1131 import parse
from test_gui_tk import fixture_root  # noqa: F401  pylint: disable=unused-import
from mediatools.core.datadir import DataDir
from mediatools.core.runlog import RunLog
from mediatools.gui.desktopentry import DesktopEntry
from mediatools.gui.queuelist import QueueList
from mediatools.gui.shrinkmonitor import ShrinkMonitor
from mediatools.shrink.shrinkqueue import ShrinkQueue

ROOT = Path(__file__).resolve().parent.parent
FAKE_PIP = """#!/bin/sh
mkdir -p "$1/bin"
printf '#!/bin/sh\\necho "mediatools %s"\\n' "${2#v}" > "$1/bin/mediatools"
chmod +x "$1/bin/mediatools"
"""


@pytest.fixture(name="world")
def fixture_world(tmp_path: Path) -> dict[str, Path]:
    """A home with the layout before 1.14.0 and a repository with tag v1.14.0.

    Args:
        tmp_path: Temporary directory (fixture).

    Returns:
        Paths ``home``, ``repo``, ``pip``, ``old_venv``, ``old_link``.
    """
    home, repo = tmp_path / "home", tmp_path / "repo"
    old_venv = home / "scripts" / "mediatools.d" / "venv"
    (old_venv / "bin").mkdir(parents=True)
    (old_venv / "pyvenv.cfg").write_text("home = /usr/bin\n")
    (old_venv / "bin" / "mediatools").write_text("#!/bin/sh\necho 'mediatools 1.13.1'\n")
    old_link = home / "scripts" / "mediatools"
    old_link.symlink_to(old_venv / "bin" / "mediatools")
    repo.mkdir()
    for args in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "x"],
                 ["tag", "v1.14.0"]):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                        *args], cwd=repo, check=True, capture_output=True, timeout=CHILD_TIMEOUT)
    pip = tmp_path / "fake-pip.sh"
    pip.write_text(FAKE_PIP)
    pip.chmod(0o755)
    return {"home": home, "repo": repo, "pip": pip, "old_venv": old_venv, "old_link": old_link}


def install(world: dict[str, Path], *, local_bin_in_path: bool) -> subprocess.CompletedProcess:
    """Run install.sh in the test home.

    Args:
        world: Paths from the fixture.
        local_bin_in_path: Put ``~/.local/bin`` into PATH.

    Returns:
        The finished process.
    """
    path = os.environ["PATH"]
    if local_bin_in_path:
        path = f"{world['home'] / '.local' / 'bin'}{os.pathsep}{path}"
    env = dict(os.environ, HOME=str(world["home"]), MEDIATOOLS_REPO=f"file://{world['repo']}",
               PIP_INSTALL_CMD=str(world["pip"]), PATH=path)
    for name in ("MEDIATOOLS_HOME", "BIN_DIR", "XDG_DATA_HOME", "XDG_CONFIG_HOME"):
        env.pop(name, None)
    return subprocess.run(["bash", str(ROOT / "install.sh"), "v1.14.0"], env=env,
                          capture_output=True, text=True, check=False, timeout=120)


def test_install_moves_to_xdg_and_keeps_the_old_link_once(world: dict[str, Path]) -> None:
    """First run: XDG venv and link, old link points to it, old venv and folder removed.

    Args:
        world: Paths from the fixture.
    """
    res = install(world, local_bin_in_path=False)
    assert res.returncode == 0, res.stderr
    home = world["home"]
    new = home / ".local" / "share" / "mediatools" / "venv" / "bin" / "mediatools"
    assert os.readlink(home / ".local" / "bin" / "mediatools") == str(new)
    assert os.readlink(world["old_link"]) == str(new)
    assert not world["old_venv"].exists() and not (home / "scripts" / "mediatools.d").exists()
    assert "not in PATH yet" in res.stdout


def test_second_run_removes_the_old_link(world: dict[str, Path]) -> None:
    """Once ~/.local/bin is in PATH, a later run removes the redirected old link.

    Args:
        world: Paths from the fixture.
    """
    install(world, local_bin_in_path=False)
    res = install(world, local_bin_in_path=True)
    assert res.returncode == 0 and "removed the old link" in res.stdout
    assert not os.path.lexists(world["old_link"])


def test_old_venv_in_use_stays(world: dict[str, Path]) -> None:
    """A process started from the old venv keeps it; the run says so.

    Args:
        world: Paths from the fixture.
    """
    sleeper = world["old_venv"] / "bin" / "python"
    shutil.copy2(shutil.which("sleep") or "/bin/sleep", sleeper)
    with subprocess.Popen([str(sleeper), "30"]) as process:
        try:
            res = install(world, local_bin_in_path=False)
        finally:
            process.kill()
    assert res.returncode == 0 and "still used by a running process" in res.stdout
    assert world["old_venv"].exists()


def test_foreign_old_link_and_unit_rewrite(world: dict[str, Path]) -> None:
    """A foreign old link is left alone; the snapshot unit is pointed to the new link.

    Args:
        world: Paths from the fixture.
    """
    world["old_link"].unlink()
    world["old_link"].symlink_to("/usr/bin/true")
    unit = world["home"] / ".config" / "systemd" / "user" / "mediatools-snapshot.service"
    unit.parent.mkdir(parents=True)
    unit.write_text(f"[Service]\nExecStart={world['old_link']} snapshot run -y\n")
    res = install(world, local_bin_in_path=True)
    assert res.returncode == 0
    assert os.readlink(world["old_link"]) == "/usr/bin/true"
    new_link = world["home"] / ".local" / "bin" / "mediatools"
    assert f"ExecStart={new_link} snapshot run -y" in unit.read_text()


def test_cache_file_moves_an_old_copy_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                           capsys: pytest.CaptureFixture) -> None:
    """XDG layout: the old cache moves to ~/.cache/mediatools; an existing one is kept.

    Args:
        tmp_path: Temporary directory (fixture).
        monkeypatch: Environment patcher (fixture).
        capsys: Output capture (fixture).
    """
    monkeypatch.delenv("MEDIATOOLS_HOME")
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in ("XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.delenv(name, raising=False)
    old = tmp_path / ".local" / "state" / "mediatools" / "state" / "shrink_codecs.tsv"
    old.parent.mkdir(parents=True)
    old.write_text("old\n")
    data = DataDir()
    path = data.cache_file("shrink_codecs.tsv")
    assert path == str(tmp_path / ".cache" / "mediatools" / "shrink_codecs.tsv")
    assert Path(path).read_text(encoding="utf-8") == "old\n" and not old.exists()
    assert "cache moved" in capsys.readouterr().err
    old.write_text("second\n")
    data.cache_file("shrink_codecs.tsv")
    assert Path(path).read_text(encoding="utf-8") == "old\n" and old.exists()
    assert "old copy not moved" in capsys.readouterr().err


def test_cache_outside_xdg_stays_in_state(datadir: Path) -> None:
    """With MEDIATOOLS_HOME the cache keeps its place in the state subfolder.

    Args:
        datadir: Test data directory (fixture).
    """
    assert DataDir().cache_file("triage_durations.tsv") == \
        str(datadir / "state" / "triage_durations.tsv")


@pytest.fixture(name="privacy_scan")
def fixture_privacy_scan() -> Iterator[object]:
    """Import tools/privacy_scan.py as a module.

    Yields:
        The module.
    """
    spec = importlib.util.spec_from_file_location("privacy_scan_1140",
                                                  ROOT / "tools" / "privacy_scan.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    yield module


def test_privacy_scan_refuses_while_old_lists_exist(privacy_scan: object, tmp_path: Path,
                                                    monkeypatch: pytest.MonkeyPatch,
                                                    capsys: pytest.CaptureFixture) -> None:
    """An old deny list in ~/scripts/mediatools.d stops the gate with exit code 2.

    Args:
        privacy_scan: The scanner module (fixture).
        tmp_path: Temporary directory (fixture).
        monkeypatch: Environment patcher (fixture).
        capsys: Output capture (fixture).
    """
    monkeypatch.delenv("MEDIATOOLS_HOME")
    monkeypatch.setenv("HOME", str(tmp_path))
    old = tmp_path / "scripts" / "mediatools.d" / "privacy_denylist.txt"
    old.parent.mkdir(parents=True)
    old.write_text("term\n")
    assert privacy_scan.main() == 2  # type: ignore[attr-defined]
    assert "refused: old configuration" in capsys.readouterr().err
    monkeypatch.setenv("MEDIATOOLS_HOME", str(tmp_path / "single"))
    assert privacy_scan.PrivacyScan.old_lists() == []  # type: ignore[attr-defined]


def test_menu_entry_prefers_the_new_link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The menu entry starts ~/.local/bin/mediatools when it exists.

    Args:
        tmp_path: Temporary directory (fixture).
        monkeypatch: Environment patcher (fixture).
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    link = tmp_path / ".local" / "bin" / "mediatools"
    link.parent.mkdir(parents=True)
    link.write_text("#!/bin/sh\n")
    assert DesktopEntry.default_program() == str(link)


def test_check_activates_the_project_venv(tmp_path: Path) -> None:
    """check.sh activates .venv of the project for itself and names missing tools.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    project = tmp_path / "project"
    (project / "tools").mkdir(parents=True)
    shutil.copy2(ROOT / "tools" / "check.sh", project / "tools" / "check.sh")
    shutil.copy2(ROOT / "tools" / "output.sh", project / "tools" / "output.sh")
    (project / ".venv" / "bin").mkdir(parents=True)
    (project / ".venv" / "bin" / "activate").write_text('export VIRTUAL_ENV="$PWD/.venv"\n')
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    res = subprocess.run(["bash", "tools/check.sh", "--fast"], cwd=project, env=env,
                         capture_output=True, text=True, check=False, timeout=CHILD_TIMEOUT)
    assert res.returncode == 2
    assert "venv: .venv (activated for this check)" in res.stdout
    assert "missing tool: pycodestyle" in res.stderr and ".venv/bin/pip install" in res.stderr


def test_stop_then_next_continues_the_queue_once(tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """After a user's stop the queue goes on only if *Stop, then next* asked for it.

    Args:
        tmp_path: Temporary directory (fixture).
        monkeypatch: Patcher (fixture).
    """
    queue = ShrinkQueue(str(tmp_path / "q.json"), python="/py")
    queue.add(["/a"])
    run = RunLog("queue-test")
    assert queue.continue_after(1, False, run, stopped=True) == 1     # plain stop: paused
    started: list[str] = []

    def fake_exec(program: str, _argv: list[str]) -> None:
        """Record the start and fail like a missing program.

        Args:
            program: Program.

        Raises:
            OSError: Always.
        """
        started.append(program)
        raise OSError("gone")

    monkeypatch.setattr(os, "execv", fake_exec)
    queue.next_after_stop()
    assert queue.continue_after(1, True, run, stopped=True) == 1      # declined wins
    assert not os.path.exists(queue.next_file)                        # used once
    queue.next_after_stop()
    assert queue.continue_after(1, False, RunLog("queue-test"), stopped=True) == 1
    assert started == ["/py"] and not os.path.exists(queue.next_file)
    queue.next_after_stop()
    queue.next_after_stop(False)
    assert not os.path.exists(queue.next_file)


def test_stop_then_next_button(root: tk.Tk, tmp_path: Path, datadir: Path) -> None:
    """The button needs a running job and a queue; it moves the selection first.

    Args:
        root: Main window (fixture).
        tmp_path: Temporary directory (fixture).
        datadir: Data directory (fixture).
    """
    queue = ShrinkQueue(str(tmp_path / "q.json"))
    view = QueueList(root, ShrinkMonitor(), parse, None, queue=queue)
    assert not view.stop_then_next() and "no shrink runs" in view.message.get()
    write_run(datadir, None)
    assert not view.stop_then_next() and "queue is empty" in view.message.get()
    queue.add([str(tmp_path / "a")])
    queue.add([str(tmp_path / "b")])
    view.refresh()
    view.jobs.selection_set("2")
    assert view.stop_then_next() and "then the next job starts" in view.message.get()
    assert queue.entries()[0]["args"] == [str(tmp_path / "b")]
    assert os.path.exists(queue.next_file)
    assert os.path.exists(ShrinkMonitor().control.stop_file)
