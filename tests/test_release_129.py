"""1.2.9: stricter validation, failure memory, kept ffmpeg logs, run lock, planner."""
import logging
import os
import sqlite3
import stat
from collections.abc import Callable
from pathlib import Path

import pytest

from conftest import make_file
from test_shrink_run import FakeEncoder, build, videos
from mediatools.cli import Cli
from mediatools.core.runcontrol import RunControl
from mediatools.shrink import ShrinkDatabase, ShrinkOptions
from mediatools.shrink.encoder import HevcEncoder

LOG = logging.getLogger("test")


class StaticProbe:
    """Probe double: every output has a video stream and 10 s."""

    def has_video(self, _path: str) -> bool:
        """Report a video stream.

        Returns:
            Always True.
        """
        return True

    def duration(self, _path: str) -> float:
        """Report 10 s.

        Returns:
            Always 10.0.
        """
        return 10.0


class CountingCheck:
    """DecodeCheck double with a fixed verdict."""

    seconds = 10.0

    def __init__(self, errors: int | None) -> None:
        """Store the verdict.

        Args:
            errors: Error count to report, None for "usable".
        """
        self.errors = errors
        self.paths: list[str] = []

    def unusable(self, path: str) -> int | None:
        """Report the stored verdict.

        Args:
            path: Video file (remembered).

        Returns:
            The stored error count.
        """
        self.paths.append(path)
        return self.errors


class FailingEncoder(FakeEncoder):
    """Encoder double that fails for one name and leaves an ffmpeg message file."""

    def __init__(self, fail: str) -> None:
        """Configure the double.

        Args:
            fail: File name whose encode fails.
        """
        super().__init__()
        self.fail = fail

    def encode(self, src: str, dst: str,
               on_progress: Callable[[float], None] | None = None) -> bool:
        """Fail for the configured name, otherwise behave like FakeEncoder.

        Args:
            src: Input file.
            dst: Output file.
            on_progress: Progress callback.

        Returns:
            False for the configured name.
        """
        if os.path.basename(src) == self.fail:
            self.calls.append(self.fail)
            Path(dst + ".ffmpeg.log").write_text("line 1\nline 2 complete\n", encoding="utf-8")
            return False
        return super().encode(src, dst, on_progress)


def sized(path: Path, size: int) -> tuple[str, str]:
    """Create an input and an output file of given sizes.

    Args:
        path: Directory.
        size: Size of the output in bytes; the input has 10000.

    Returns:
        Paths of input and output.
    """
    return make_file(path / "in.mp4", b"i" * 10000), make_file(path / "out.mp4", b"o" * size)


# -- 1. stricter validation ------------------------------------------------
def test_tiny_output_is_rejected(tmp_path: Path) -> None:
    """An output below 2 % of the source never replaces the original.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    enc = HevcEncoder(probe=StaticProbe(), logger=LOG)  # type: ignore[arg-type]
    src, dst = sized(tmp_path, 199)
    assert not enc.validate(src, dst)
    src, dst = sized(tmp_path, 200)
    assert enc.validate(src, dst)


def test_undecodable_output_is_rejected(tmp_path: Path) -> None:
    """With a decode check, an output that does not decode is rejected.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    src, dst = sized(tmp_path, 5000)
    bad = HevcEncoder(probe=StaticProbe(), logger=LOG,  # type: ignore[arg-type]
                      decode=CountingCheck(25))  # type: ignore[arg-type]
    assert not bad.validate(src, dst)
    good_check = CountingCheck(None)
    good = HevcEncoder(probe=StaticProbe(), logger=LOG,  # type: ignore[arg-type]
                       decode=good_check)  # type: ignore[arg-type]
    assert good.validate(src, dst) and good_check.paths == [dst]


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


def test_message_file_kept_on_invalid_output_removed_on_success(tmp_path: Path) -> None:
    """ffmpeg's message file survives a failed validation and goes after a good one.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    src = make_file(tmp_path / "in.mp4", b"i" * 10000)
    tiny = script(tmp_path / "ff_tiny", 'for a; do last="$a"; done; '
                                        'echo warn >&2; printf x > "$last"')
    enc = HevcEncoder(probe=StaticProbe(), logger=LOG, ffmpeg=tiny)  # type: ignore[arg-type]
    out = str(tmp_path / "out.mp4")
    assert not enc.encode(src, out)
    assert Path(out + ".ffmpeg.log").read_text(encoding="utf-8") == "warn\n"
    full = script(tmp_path / "ff_full", 'for a; do last="$a"; done; '
                                        'head -c 5000 /dev/zero > "$last"')
    enc = HevcEncoder(probe=StaticProbe(), logger=LOG, ffmpeg=full)  # type: ignore[arg-type]
    out = str(tmp_path / "out2.mp4")
    assert enc.encode(src, out) and not os.path.exists(out + ".ffmpeg.log")


# -- 2. failure memory -----------------------------------------------------
def test_database_failure_rows(tmp_path: Path) -> None:
    """Failures are matched by name and size, cleared on success, absent in old files.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    video = make_file(tmp_path / "v.mp4", b"v" * 10)
    old = ShrinkDatabase(str(tmp_path))
    with sqlite3.connect(old.path) as conn:          # database of an older version
        conn.execute(ShrinkDatabase.SCHEMA)
    conn.close()
    assert old.failure(video) is None
    database = ShrinkDatabase(str(tmp_path))
    database.ensure()
    database.record_failure(video, "BROKEN", "42 decoder errors")
    assert ShrinkDatabase(str(tmp_path)).failure(video) == "BROKEN"
    make_file(tmp_path / "v.mp4", b"v" * 11)          # changed file: tried again
    assert ShrinkDatabase(str(tmp_path)).failure(video) is None
    database.clear_failure(video)
    database.clear_failure(video)                     # nothing left: no effect
    assert not ShrinkDatabase(str(tmp_path)).failures()
    assert ShrinkDatabase(str(tmp_path / "none")).failures() == {}


def test_failed_video_is_left_out_until_retry(tmp_path: Path,
                                              caplog: pytest.LogCaptureFixture) -> None:
    """A failed video is skipped next time; --retry-failed tries it and clears the row.

    Args:
        tmp_path: Temporary directory (fixture).
        caplog: Log capture (fixture).
    """
    paths = videos(tmp_path)
    shrinker, _encoder = build(tmp_path, encoder=FailingEncoder("medium.mp4"))
    assert shrinker.run() == 2
    database = ShrinkDatabase(str(tmp_path / "c/a"))
    assert database.failure(paths["medium.mp4"]) == "FAILED"
    again, encoder = build(tmp_path)
    with caplog.at_level(logging.INFO, logger="test"):
        assert again.run() == 0
    assert not encoder.calls and again.stats["failed_before"] == 1
    assert "FAILED BEFORE" in caplog.text and "--retry-failed" in caplog.text
    retry, encoder = build(tmp_path, ShrinkOptions(retry_failed=True))
    assert retry.run() == 0 and encoder.calls == ["medium.mp4"]
    assert ShrinkDatabase(str(tmp_path / "c/a")).failure(paths["medium.mp4"]) is None


def test_broken_video_is_recorded_as_failure(tmp_path: Path) -> None:
    """A BROKEN video is noted as failure, not as processed.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    paths = videos(tmp_path)
    shrinker, encoder = build(tmp_path)
    shrinker.state["decode"] = CountingCheck(30)  # type: ignore[typeddict-item]
    assert shrinker.run() == 0 and not encoder.calls
    database = ShrinkDatabase(str(tmp_path / "c/a"))
    assert database.failure(paths["small.mp4"]) == "BROKEN"
    assert not database.is_done(paths["small.mp4"])


def test_review_mode_records_no_failure(tmp_path: Path) -> None:
    """--keep-copies writes nothing into the databases, failures included.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    paths = videos(tmp_path)
    shrinker, _encoder = build(tmp_path, ShrinkOptions(keep_copies=True),
                               encoder=FailingEncoder("large.mp4"))
    assert shrinker.run() == 2
    assert ShrinkDatabase(str(tmp_path / "c/b")).failure(paths["large.mp4"]) is None


# -- 3. complete ffmpeg messages kept ----------------------------------------
def test_ffmpeg_messages_of_a_failed_encode_are_kept(tmp_path: Path) -> None:
    """The whole message file is moved to the log folder; without one it is deleted.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    videos(tmp_path)
    shrinker, _encoder = build(tmp_path, encoder=FailingEncoder("large.mp4"))
    shrinker.state["ffmpeg_logs"] = str(tmp_path / "logs/ffmpeg")
    assert shrinker.run() == 2
    kept = list((tmp_path / "logs/ffmpeg").iterdir())
    assert len(kept) == 1 and kept[0].name.endswith("_large.mp4.ffmpeg.log")
    assert kept[0].read_text(encoding="utf-8") == "line 1\nline 2 complete\n"
    assert not (tmp_path / "c/b/newShrinkedFiles").exists()


def test_ffmpeg_messages_deleted_without_log_folder(tmp_path: Path) -> None:
    """Without a log folder nothing is left in the work folder.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    videos(tmp_path)
    shrinker, _encoder = build(tmp_path, ShrinkOptions(retry_failed=True),
                               encoder=FailingEncoder("large.mp4"))
    assert shrinker.run() == 2
    assert not (tmp_path / "c/b/newShrinkedFiles").exists()


# -- 4. run lock ---------------------------------------------------------
def test_second_run_is_refused(capsys: pytest.CaptureFixture) -> None:
    """A live shrink blocks start() and the CLI; a dead PID file does not.

    Args:
        capsys: Output capture (fixture).
    """
    control = RunControl("shrink")
    Path(control.pid_file).write_text(f"{os.getppid()}\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="already running"):
        RunControl("shrink").start()
    assert Cli().main(["shrink", "--foreground", "-y"]) == 3
    assert "already running" in capsys.readouterr().out
    os.remove(control.pid_file)
    mine = RunControl("shrink")
    mine.start()
    mine.start()                                       # own registration: allowed
    mine.finish()
