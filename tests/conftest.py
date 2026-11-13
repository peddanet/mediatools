"""Shared fixtures: isolated data directory, generated media, fake player."""
import gc
import os
import shutil
import stat
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
HAVE_RSYNC = shutil.which("rsync") is not None
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not installed")
needs_rsync = pytest.mark.skipif(not HAVE_RSYNC, reason="rsync not installed")


@pytest.fixture(autouse=True)
def collect_in_main_thread() -> Iterator[None]:
    """Detach Tk variables left over by a test, in the main thread.

    A variable with a trace callback stays alive after its window is
    destroyed: the Tcl interpreter holds the callback, which holds the form,
    which holds the variable. When such a variable is finalised later, the
    garbage collector may run in a worker thread (e.g. of a parallel shrink
    test), where Tk refuses with "main thread is not in main loop". Cutting
    the link to the interpreter here makes the finaliser a no-op. Only the
    tests share one process between Tk and threads; the program runs the
    GUI and a shrink in separate processes.

    Yields:
        Nothing.
    """
    yield
    gc.collect()
    tkinter = sys.modules.get("tkinter")
    if tkinter is not None:
        for item in gc.get_objects():
            if isinstance(item, tkinter.Variable):
                item._tk = None              # pylint: disable=protected-access


@pytest.fixture(autouse=True)
def datadir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Give every test its own temporary ``MEDIATOOLS_HOME``.

    Args:
        tmp_path: Per-test temporary directory (pytest fixture).
        monkeypatch: Environment patcher (pytest fixture).

    Returns:
        Path of the data directory.
    """
    home = tmp_path / "mediatools_home"
    monkeypatch.setenv("MEDIATOOLS_HOME", str(home))
    return home


def make_video(path: Path, seconds: int = 2, codec: str = "libx264") -> str:
    """Create a small test video with ffmpeg.

    Args:
        path: Output file.
        seconds: Duration in seconds.
        codec: ffmpeg video encoder, e.g. ``libx264`` or ``libx265``.

    Returns:
        The path as string.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
           "-i", f"testsrc=duration={seconds}:size=160x120:rate=10", "-c:v", codec]
    if codec == "libx265":
        cmd += ["-x265-params", "log-level=error"]
    subprocess.run(cmd + [str(path)], check=True)
    return str(path)


def make_file(path: Path, content: bytes = b"x") -> str:
    """Create a small file.

    Args:
        path: File to create (parents are created).
        content: Bytes to write.

    Returns:
        The path as string.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Path(path).write_bytes(content)
    return str(path)


def read(path: str) -> bytes:
    """Read a file completely.

    Args:
        path: File to read.

    Returns:
        Its content as bytes.
    """
    return Path(path).read_bytes()


@pytest.fixture
def fake_player(tmp_path: Path) -> str:
    """Provide a stand-in GUI player.

    The script stays alive like a player window; SIGUSR1 simulates the user
    closing the window (exit code 0).

    Args:
        tmp_path: Per-test temporary directory (pytest fixture).

    Returns:
        Path of the executable script.
    """
    script = tmp_path / "fakeplayer"
    script.write_text("#!/bin/bash\n"
                      "trap 'kill $c 2>/dev/null; exit 0' USR1\n"
                      "trap 'kill $c 2>/dev/null; exit 143' TERM\n"
                      "sleep 600 & c=$!\nwait $c\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)
