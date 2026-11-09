"""Background mode, status file and ``mediatools attach``."""
import io
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from mediatools.cli import Cli
from mediatools.core.attachview import AttachView
from mediatools.core.progressbar import ProgressBar
from mediatools.core.runcontrol import RunControl

CHILD = """
import os, sys, time
from mediatools.core.runcontrol import RunControl
control = RunControl("job", background=True, log_path=sys.argv[1])
control.start()
print("before detach")
control.begin_work()
print("console line")
with open(sys.argv[2], "w") as out:
    out.write(f"{os.getpid()} {os.getsid(0)} {control.active_pid()} {control.active_log()}")
time.sleep(1.0)
control.finish()
"""


def wait_for(path: Path, seconds: float = 10.0) -> str:
    """Wait until a file has content.

    Args:
        path: File to wait for.
        seconds: Maximum wait.

    Returns:
        File content.

    Raises:
        AssertionError: If the file stays empty.
    """
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if path.exists() and path.read_text(encoding="utf-8"):
            return path.read_text(encoding="utf-8")
        time.sleep(0.05)
    raise AssertionError(f"{path} not written")


def test_detach_continues_in_new_session(datadir: Path, tmp_path: Path) -> None:
    """The caller exits at once; the child runs on in its own session.

    Args:
        datadir: Temporary data directory (fixture).
        tmp_path: Temporary directory (fixture).
    """
    marker = tmp_path / "child.txt"
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))
    started = time.monotonic()
    parent = subprocess.run([sys.executable, "-c", CHILD, "/x/run.log", str(marker)],
                            capture_output=True, text=True, env=env, check=True, timeout=30)
    assert time.monotonic() - started < 5
    assert "before detach" in parent.stdout
    assert "job continues in the background" in parent.stdout
    assert "mediatools attach" in parent.stdout and "/x/run.log" in parent.stdout
    assert "console line" not in parent.stdout
    pid, sid, registered, log = wait_for(marker).split()
    assert parent.returncode == 0 and pid == sid == registered and log == "/x/run.log"
    console = Path(RunControl("job").console_file)
    assert "console line" in wait_for(console)
    end = time.monotonic() + 10
    while RunControl("job").active_pid() is not None and time.monotonic() < end:
        time.sleep(0.05)
    assert RunControl("job").active_pid() is None      # child unregistered itself
    assert datadir.exists()


def test_status_file_is_written_rate_limited(tmp_path: Path) -> None:
    """Both progress lines go to the status file, at most once per second.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    progress = ProgressBar(1000, 2, 1000.0, stream=io.StringIO())
    progress.status_path = str(tmp_path / "s.status")
    progress.show("clip.mp4", 0.5, 100)
    lines = Path(progress.status_path).read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("total [") and "clip.mp4" in lines[1]
    progress.show("other.mp4", 0.6, 100)                   # within the interval: unchanged
    assert "clip.mp4" in Path(progress.status_path).read_text(encoding="utf-8")
    progress.status_path = str(tmp_path / "missing-dir" / "s.status")
    progress.last_status = 0.0
    progress.show("x", 0.1, 100)                           # write error is ignored


class TtyBuffer(io.StringIO):
    """StringIO that claims to be a terminal."""

    def isatty(self) -> bool:
        """Pretend to be a terminal.

        Returns:
            Always True.
        """
        return True


def test_attach_shows_log_and_ends_with_the_run(datadir: Path, tmp_path: Path) -> None:
    """attach prints the backlog, new lines and the status, until the run ends.

    Args:
        datadir: Temporary data directory (fixture).
        tmp_path: Temporary directory (fixture).
    """
    log = tmp_path / "run.log"
    log.write_text("old line\n", encoding="utf-8")
    # reaped by a thread (a zombie would still look alive), hence no 'with'
    worker = subprocess.Popen(  # pylint: disable=consider-using-with
        [sys.executable, "-c", "import time; time.sleep(1.5)"])
    threading.Thread(target=worker.wait).start()
    control = RunControl("shrink")
    Path(control.pid_file).write_text(f"{worker.pid}\n{log}\n", encoding="utf-8")
    Path(control.status_file).write_text("total [...] 5 %\nfile 10 % of x\n", encoding="utf-8")

    def append_later() -> None:
        """Append a full and an unfinished line while the view runs."""
        time.sleep(0.3)
        with open(log, "a", encoding="utf-8") as handle:
            handle.write("new line\npartial")

    threading.Thread(target=append_later).start()
    out = TtyBuffer()
    assert AttachView(control, out, poll=0.1).run() == 0
    text = out.getvalue()
    assert "old line" in text and "new line" in text and "partial" not in text
    assert "total [...] 5 %" in text and "file 10 % of x" in text
    assert text.rstrip().endswith("shrink run has ended")
    assert datadir.exists()


def test_attach_without_run_and_ctrl_c(datadir: Path, monkeypatch: pytest.MonkeyPatch,
                                       capsys: pytest.CaptureFixture) -> None:
    """No run: exit 1. Ctrl+C: leaves the view, exit 0.

    Args:
        datadir: Temporary data directory (fixture).
        monkeypatch: Patch helper (fixture).
        capsys: Output capture (fixture).
    """
    assert Cli().main(["attach"]) == 1
    assert "no shrink run is active" in capsys.readouterr().out
    control = RunControl("shrink", log_path=None)
    control.start()

    def interrupt(_seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(time, "sleep", interrupt)
    assert Cli().main(["front"]) == 0
    assert "left the view - shrink keeps running" in capsys.readouterr().out
    control.finish()
    assert datadir.exists()


def test_attach_shows_what_the_terminal_showed() -> None:
    """DEBUG lines are dropped, INFO lines lose time stamp and level."""
    text = ("2026-11-09 11:22:46,612 INFO    │ row │\n"
            "2026-11-09 11:22:46,612 DEBUG   ffmpeg: ...\n"
            "2026-11-09 11:22:42,538 WARNING lock refused\n"
            "Traceback (most recent call last):\n")
    assert AttachView.terminal_lines(text) == [
        "│ row │\n", "WARNING lock refused\n", "Traceback (most recent call last):\n"]
