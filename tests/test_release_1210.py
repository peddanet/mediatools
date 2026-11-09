"""1.2.10: a running shrink switches to a newly installed release between two files."""
import logging
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from conftest import make_file
from test_shrink_run import FakeEncoder, build, videos
from mediatools import __version__
from mediatools.cli import Cli
from mediatools.core.runcontrol import RunControl
from mediatools.core.runlog import RunLog
from mediatools.core.selfrestart import SelfRestart
from mediatools.shrink import Shrinker

LOG = logging.getLogger("test")


class FakeRestart:
    """SelfRestart double with a fixed installed version."""

    def __init__(self, version: str | None) -> None:
        """Store the answer.

        Args:
            version: Version the installed release reports, None if it fails.
        """
        self.version = version

    def installed_version(self) -> str | None:
        """Report the stored version.

        Returns:
            The stored version.
        """
        return self.version


class ReloadingEncoder(FakeEncoder):
    """Encoder double that requests a reload while encoding the first file."""

    def __init__(self, control: RunControl) -> None:
        """Remember the job control.

        Args:
            control: Control of the running job.
        """
        super().__init__()
        self.control = control

    def encode(self, src: str, dst: str,
               on_progress: Callable[[float], None] | None = None) -> bool:
        """Request a reload during the first encode, then behave like FakeEncoder.

        Args:
            src: Input file.
            dst: Output file.
            on_progress: Progress callback.

        Returns:
            Result of FakeEncoder.encode.
        """
        if not self.calls:
            assert RunControl("shrink").request_restart() == os.getpid()
        return super().encode(src, dst, on_progress)


def reloading_run(tmp_path: Path, version: str | None) -> tuple[Shrinker, ReloadingEncoder, int]:
    """Run a shrink whose first encode is followed by a reload request.

    Args:
        tmp_path: Temporary directory.
        version: Version the fake installed release reports.

    Returns:
        Shrinker, encoder and exit code.
    """
    videos(tmp_path)
    control = RunControl("shrink")
    encoder = ReloadingEncoder(control)
    shrinker, _unused = build(tmp_path, encoder=encoder)
    shrinker.control = control
    shrinker.state["restart"] = FakeRestart(version)  # type: ignore[typeddict-item]
    return shrinker, encoder, shrinker.run()


def test_switch_after_the_current_file(tmp_path: Path) -> None:
    """The current file is finished; then the job hands over and asks for the switch.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    shrinker, encoder, code = reloading_run(tmp_path, "9.9.9")
    control = RunControl("shrink")
    assert code == Shrinker.RESTART and encoder.calls == ["large.mp4"]
    assert shrinker.stats["shrunk"] == 1
    assert control.active_pid() == os.getpid()           # registration handed over
    assert not os.path.exists(control.restart_file)
    os.remove(control.pid_file)


@pytest.mark.parametrize("version", [None, __version__])
def test_no_switch_to_a_failing_or_same_release(tmp_path: Path, version: str | None,
                                                caplog: pytest.LogCaptureFixture) -> None:
    """A release that does not start, or the same version, is ignored; the run continues.

    Args:
        tmp_path: Temporary directory (fixture).
        version: Answer of the installed release.
        caplog: Log capture (fixture).
    """
    with caplog.at_level(logging.INFO, logger="test"):
        _shrinker, encoder, code = reloading_run(tmp_path, version)
    assert code == 0 and len(encoder.calls) == 3
    assert "reload requested" in caplog.text
    assert not os.path.exists(RunControl("shrink").restart_file)
    assert RunControl("shrink").active_pid() is None


def test_requests_without_restart_helper_are_ignored(tmp_path: Path) -> None:
    """Without a SelfRestart the request file stays and is removed at the end.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    videos(tmp_path)
    control = RunControl("shrink")
    shrinker, encoder = build(tmp_path, encoder=ReloadingEncoder(control))
    shrinker.control = control
    assert shrinker.run() == 0 and len(encoder.calls) == 3
    assert not os.path.exists(control.restart_file)


def test_command_line_of_the_new_process() -> None:
    """The new process gets the same options, plus foreground, -y and the log."""
    restart = SelfRestart(["shrink", "/c", "--order", "path", "-y", "--foreground",
                           "--continue-log", "/old.log", "--continue-log=/x"], "/run.log",
                          python="/venv/bin/python")
    assert restart.command() == ["/venv/bin/python", "-m", "mediatools", "shrink", "/c",
                                 "--order", "path", "--foreground", "-y",
                                 "--continue-log", "/run.log"]


def fake_python(tmp_path: Path, body: str) -> str:
    """Write an executable that stands in for the interpreter.

    Args:
        tmp_path: Temporary directory.
        body: Shell commands.

    Returns:
        Path of the script.
    """
    path = make_file(tmp_path / "python", ("#!/bin/sh\n" + body + "\n").encode())
    os.chmod(path, 0o755)
    return path


@pytest.mark.parametrize("body, expected", [
    ("echo 'mediatools 1.2.11'", "1.2.11"),
    ("echo 'mediatools 1.2.11 (source checkout /x, not an installed release)'", "1.2.11"),
    ("echo 'Traceback'; exit 1", None),
    ("echo 'something else'", None),
])
def test_installed_version(tmp_path: Path, body: str, expected: str | None) -> None:
    """The installed release is asked for its version before the switch.

    Args:
        tmp_path: Temporary directory (fixture).
        body: Behaviour of the fake interpreter.
        expected: Parsed version.
    """
    assert SelfRestart([], "x", fake_python(tmp_path, body)).installed_version() == expected


def test_installed_version_missing_interpreter(tmp_path: Path) -> None:
    """A vanished interpreter gives no version.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    assert SelfRestart([], "x", str(tmp_path / "none")).installed_version() is None


def test_execute_replaces_the_process() -> None:
    """A real exec in a child process runs the new command line."""
    code = ("import sys; from mediatools.core.selfrestart import SelfRestart; "
            "SelfRestart(['shrink', '--help'], '/tmp/x.log').execute()")
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         check=False, env=env, timeout=60)
    assert res.returncode == 0 and "--retry-failed" in res.stdout


def test_reload_command(capsys: pytest.CaptureFixture) -> None:
    """mediatools reload asks a live shrink, and leaves nothing behind without one.

    Args:
        capsys: Output capture (fixture).
    """
    assert Cli().main(["reload"]) == 1
    assert "nothing to reload" in capsys.readouterr().out
    control = RunControl("shrink")
    control.start()
    assert Cli().main(["reload"]) == 0 and control.restart_requested()
    control.handover()
    assert not control.restart_requested() and control.active_pid() == os.getpid()
    control.request_restart()
    control.clear_restart()
    assert not control.restart_requested()
    control.finish()


def test_cli_switches_after_restart_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                         capsys: pytest.CaptureFixture) -> None:
    """cmd_shrink hands RESTART to the switch; a failed exec returns 1 with a hint.

    Args:
        tmp_path: Temporary directory (fixture).
        monkeypatch: Patcher (fixture).
        capsys: Output capture (fixture).
    """
    monkeypatch.setattr(Shrinker, "run", lambda self, confirm=None: Shrinker.RESTART)
    monkeypatch.setattr(Shrinker, "install_signal_handlers", lambda self: None)

    def fail(_self: SelfRestart) -> None:
        raise OSError("gone")

    monkeypatch.setattr(SelfRestart, "execute", fail)
    assert Cli().main(["shrink", str(tmp_path), "--foreground", "-y"]) == 1
    assert "switch to the installed release failed" in capsys.readouterr().err


def test_log_is_continued() -> None:
    """The new process appends to the log of the old one."""
    first = RunLog("shrink")
    first.logger.info("before the switch")
    first.close()
    second = RunLog("shrink", continue_path=first.path)
    second.logger.info("after the switch")
    second.close()
    text = Path(first.path).read_text(encoding="utf-8")
    assert "before the switch" in text and "after the switch" in text
    assert second.path == first.path
