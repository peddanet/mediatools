"""Stop request from another terminal: RunControl, Shrinker and ``mediatools stop``."""
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from test_shrink_run import FakeEncoder, build, videos
from mediatools.cli import Cli
from mediatools.core.runcontrol import RunControl


def dead_pid() -> int:
    """Return the PID of a process that has already ended.

    Returns:
        PID that no living process uses (just reaped).
    """
    child = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                           check=True, capture_output=True, text=True)
    return int(child.stdout)


def test_start_finish_and_active_pid() -> None:
    """start() registers this process and drops a stale request; finish() cleans up."""
    control = RunControl("job")
    Path(control.stop_file).write_text("stale\n", encoding="utf-8")
    assert control.active_pid() is None
    control.start()
    assert not os.path.exists(control.stop_file)
    assert control.active_pid() == os.getpid()
    assert control.request_stop() == os.getpid()
    assert control.stop_requested(force=True)
    control.finish()
    assert not os.path.exists(control.pid_file)
    assert not os.path.exists(control.stop_file)


def test_no_stop_file_without_a_running_job() -> None:
    """A stale or garbled PID file counts as 'no job'; no stop file is left behind."""
    control = RunControl("job")
    Path(control.pid_file).write_text(f"{dead_pid()}\n", encoding="utf-8")
    assert control.request_stop() is None
    Path(control.pid_file).write_text("garbage\n", encoding="utf-8")
    assert control.request_stop() is None
    assert not os.path.exists(control.stop_file)


def test_foreign_live_process_counts_as_running(monkeypatch: pytest.MonkeyPatch) -> None:
    """PermissionError from kill(pid, 0) means: alive, owned by another user.

    Args:
        monkeypatch: Patch helper (fixture).
    """
    control = RunControl("job")
    Path(control.pid_file).write_text("1\n", encoding="utf-8")

    def denied(_pid: int, _sig: int) -> None:
        raise PermissionError

    monkeypatch.setattr(os, "kill", denied)
    assert control.active_pid() == 1


def test_finish_keeps_pid_file_of_another_run() -> None:
    """finish() of an old run does not unregister a newer one."""
    control = RunControl("job")
    Path(control.pid_file).write_text(f"{os.getppid()}\n", encoding="utf-8")
    control.finish()
    assert os.path.exists(control.pid_file)


def test_polling_is_rate_limited() -> None:
    """Without force the stop file is checked at most every POLL_SECONDS."""
    control = RunControl("job")
    control.start()
    assert not control.stop_requested(force=True)
    control.request_stop()
    assert not control.stop_requested()            # within the interval
    control.last_poll = 0.0
    assert control.stop_requested()
    control.finish()


class StopEncoder(FakeEncoder):
    """Encoder double that runs ``mediatools stop`` while encoding a file."""

    def __init__(self, stop_at: str) -> None:
        """Configure the double.

        Args:
            stop_at: File name during whose encoding the stop is requested.
        """
        super().__init__()
        self.stop_at = stop_at
        self.cli_result: int | None = None

    def encode(self, src: str, dst: str,
               on_progress: Callable[[float], None] | None = None) -> bool:
        """Request the stop from 'another terminal', then encode.

        Args:
            src: Input file.
            dst: Output file.
            on_progress: Progress callback.

        Returns:
            Result of the base double.
        """
        if os.path.basename(src) == self.stop_at:
            self.cli_result = Cli().main(["stop"])
            assert self.shrinker is not None and self.shrinker.control is not None
            self.shrinker.control.last_poll = 0.0  # next progress tick checks
        return super().encode(src, dst, on_progress)


def test_stop_command_finishes_current_file_and_ends(
        tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """``mediatools stop`` during a file: that file is completed, the run ends with 1.

    Args:
        tmp_path: Temporary directory (fixture).
        capsys: Output capture (fixture).
    """
    paths = videos(tmp_path)
    encoder = StopEncoder("large.mp4")
    shrinker, _ = build(tmp_path, encoder=encoder)
    shrinker.control = RunControl("shrink")
    assert shrinker.run() == 1
    assert encoder.cli_result == 0
    assert "stop requested: shrink (pid" in capsys.readouterr().out
    assert encoder.calls == ["large.mp4"]
    assert Path(paths["large.mp4"]).read_bytes() == b"shrunk:large.mp4"
    assert shrinker.display is not None and "STOPPING" in shrinker.display.note
    assert not os.path.exists(shrinker.control.pid_file)
    assert not os.path.exists(shrinker.control.stop_file)
    resumed, encoder2 = build(tmp_path)
    resumed.control = RunControl("shrink")
    assert resumed.run() == 0 and encoder2.calls == ["medium.mp4", "small.mp4"]


def test_stop_command_without_running_shrink(capsys: pytest.CaptureFixture) -> None:
    """``mediatools stop`` with nothing running: message, exit code 1.

    Args:
        capsys: Output capture (fixture).
    """
    assert Cli().main(["stop"]) == 1
    assert "no shrink run is active" in capsys.readouterr().out
    assert not os.path.exists(RunControl("shrink").stop_file)
