"""Shared fixtures: isolated data directory, generated media, fake player."""
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
HAVE_RSYNC = shutil.which("rsync") is not None
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not installed")
needs_rsync = pytest.mark.skipif(not HAVE_RSYNC, reason="rsync not installed")


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
