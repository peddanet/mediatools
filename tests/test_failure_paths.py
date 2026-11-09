"""Error handling of sync, removal and encoding: every failure is logged, nothing lost."""
import logging
import os
import stat
from pathlib import Path

import pytest

from conftest import make_file, needs_rsync
from mediatools.core.prompt import Prompt
from mediatools.shrink.encoder import HevcEncoder
from mediatools.sync import syncer as syncer_module
from mediatools.sync.fscheck import FsCheck
from mediatools.sync.rsync_runner import RsyncRunner
from mediatools.sync.syncer import Syncer
from mediatools.triage.remover import FileRemover

LOG = logging.getLogger("test")


def script(path: Path, body: str) -> str:
    """Write an executable shell script.

    Args:
        path: Script file.
        body: Shell commands after the shebang line.

    Returns:
        The path as string.
    """
    path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


class VfatFs(FsCheck):
    """File-system helper that reports every path as vfat."""

    def fs_type(self, path: str) -> str | None:
        """Report vfat.

        Args:
            path: Any path.

        Returns:
            ``"vfat"``.
        """
        return "vfat"


class FakeProbe:
    """Probe with fixed answers.

    Attributes:
        video: Answer of :meth:`has_video`.
        durations: Durations returned in call order.
    """

    def __init__(self, video: bool, durations: list[float | None]):
        """Store the answers.

        Args:
            video: Whether the output has a video stream.
            durations: Durations returned one after the other.
        """
        self.video = video
        self.durations = list(durations)

    def has_video(self, path: str) -> bool:
        """Return the configured stream answer.

        Args:
            path: Ignored.

        Returns:
            The configured value.
        """
        del path
        return self.video

    def duration(self, path: str) -> float | None:
        """Return the next configured duration.

        Args:
            path: Ignored.

        Returns:
            Duration in seconds or None.
        """
        del path
        return self.durations.pop(0) if self.durations else None


def test_sync_refuses_missing_rsync_and_bad_paths(tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing rsync, missing source and nested paths stop before any transfer.

    Args:
        tmp_path: Temporary directory (fixture).
        monkeypatch: Patcher (fixture).
    """
    src = tmp_path / "src"
    os.makedirs(src)
    assert not Syncer(str(tmp_path / "missing"), str(tmp_path / "t"), LOG).check()
    assert not Syncer(str(src), str(src / "inner"), LOG).check()
    assert not Syncer(str(src), str(tmp_path / "t"), LOG, fs=VfatFs()).check()
    monkeypatch.setattr(syncer_module.shutil, "which", lambda tool: None)
    assert Syncer(str(src), str(tmp_path / "t"), LOG).run() == 1


@needs_rsync
def test_sync_failed_dry_run_and_declined_transfer(tmp_path: Path,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """A failing dry run returns 1; a declined transfer copies nothing.

    Args:
        tmp_path: Temporary directory (fixture).
        monkeypatch: Patcher (fixture).
    """
    src, dst = tmp_path / "src", tmp_path / "dst"
    make_file(src / "a.jpg")
    declined = Prompt(input_func=lambda _q: "n")
    assert Syncer(str(src), str(dst), LOG, declined).run() == 0
    assert not os.path.exists(dst / "a.jpg")
    monkeypatch.setattr(RsyncRunner, "dry_run", lambda self: {"returncode": 23, "stderr": "x"})
    assert Syncer(str(src), str(dst), LOG, declined).run() == 1


def test_remove_failures_are_logged(tmp_path: Path) -> None:
    """A missing file, a failing gio and an occupied name are reported as failure.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    assert not FileRemover(True, LOG).remove(str(tmp_path / "missing.mp4"))
    gio = script(tmp_path / "gio", "exit 1")
    victim = make_file(tmp_path / "a.mp4")
    assert not FileRemover(False, LOG, gio).discard(victim)
    assert os.path.exists(victim)
    ok_gio = script(tmp_path / "gio_ok", "exit 0")   # "trashes" without removing
    assert not FileRemover(False, LOG, ok_gio).discard(victim)
    assert os.path.getsize(victim) == 1             # the full file is never overwritten


def test_encoder_failure_and_missing_output(tmp_path: Path) -> None:
    """A failing ffmpeg or one without output keeps the original.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    src = make_file(tmp_path / "in.mp4")
    failing = script(tmp_path / "ffmpeg_fail", "echo broken >&2; exit 1")
    enc = HevcEncoder(probe=FakeProbe(True, [2.0]), logger=LOG, ffmpeg=failing)
    assert not enc.encode(src, str(tmp_path / "out.mp4"))
    silent = script(tmp_path / "ffmpeg_silent", "exit 0")
    enc = HevcEncoder(probe=FakeProbe(True, [2.0]), logger=LOG, ffmpeg=silent)
    assert not enc.encode(src, str(tmp_path / "out2.mp4"))
    enc.abort()                                       # nothing running: no effect


@pytest.mark.parametrize("video, durations, expected", [
    (False, [2.0, 2.0], False),        # no video stream in the output
    (True, [10.0, 4.0], False),        # duration mismatch
    (True, [None, 4.0], True),         # duration unknown: stream check decides
    (True, [10.0, 10.0], True),        # matching
])
def test_encoder_validation(tmp_path: Path, video: bool, durations: list, expected: bool) -> None:
    """Validation compares video stream and duration of output and input.

    Args:
        tmp_path: Temporary directory (fixture).
        video: Whether the output has a video stream.
        durations: Input and output duration.
        expected: Validation result.
    """
    enc = HevcEncoder(probe=FakeProbe(video, durations), logger=LOG)
    assert enc.validate(str(tmp_path / "a"), str(tmp_path / "b")) is expected


def test_encoder_logs_tail_of_missing_file(tmp_path: Path) -> None:
    """A missing ffmpeg message file is ignored.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    log_path = str(tmp_path / "none.log")
    HevcEncoder(logger=LOG)._log_tail(log_path)  # pylint: disable=protected-access
    assert not os.path.exists(log_path)


def test_ffmpeg_runs_with_lowest_priority(tmp_path: Path) -> None:
    """ffmpeg is started with niceness 19, so the desktop stays responsive.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    src = make_file(tmp_path / "in.mp4")
    report = tmp_path / "niceness.txt"
    fake = script(tmp_path / "ffmpeg_nice", f"nice > '{report}'; exit 1")
    HevcEncoder(probe=FakeProbe(True, [2.0]), logger=LOG, ffmpeg=fake).encode(
        src, str(tmp_path / "out.mp4"))
    base = os.nice(0)
    assert int(report.read_text(encoding="utf-8")) == min(19, base + 19)
