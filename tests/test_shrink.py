"""Unit tests for mediatools.shrink."""
import logging
import os
import sqlite3
from pathlib import Path

from conftest import make_file, make_video, needs_ffmpeg, read
from mediatools.core.probe import MediaProbe
from mediatools.shrink import HevcEncoder, ProgressReport, ShrinkDatabase, Shrinker

LOG = logging.getLogger("test")


# -- ShrinkDatabase ------------------------------------------------------
def test_database_record_and_lookup(tmp_path: Path) -> None:
    """A recorded file is done until its size changes.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    path = make_file(tmp_path / "a.mp4", b"abc")
    database = ShrinkDatabase(str(tmp_path))
    database.ensure()
    assert not database.is_done(path)
    database.record(path)
    assert database.is_done(path)
    make_file(tmp_path / "a.mp4", b"changed size")
    assert not database.is_done(path)


def test_database_migrates_old_schema(tmp_path: Path) -> None:
    """Old databases are backed up and get a PRIMARY KEY.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    conn = sqlite3.connect(tmp_path / ShrinkDatabase.NAME)
    conn.execute("CREATE TABLE files (path text, size integer, modified text)")
    conn.executemany("INSERT INTO files VALUES (?,?,?)",
                     [("/a", 1, "t1"), ("/a", 2, "t2"), (None, 3, "t3")])
    conn.commit()
    conn.close()
    database = ShrinkDatabase(str(tmp_path))
    database.ensure()
    assert database.migrated_backup and os.path.exists(database.migrated_backup)
    assert database.rows() == [("/a", 2, "t2")]
    database.record(make_file(tmp_path / "b.mp4"))      # UPSERT works now


# -- Shrinker / HevcEncoder (need ffmpeg) --------------------------------
@needs_ffmpeg
def test_shrinker_full_cycle(tmp_path: Path) -> None:
    """Encode, HEVC skip, failure handling, _flat skip and DB precedence.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    avc = make_video(tmp_path / "u/a.mp4", 3)
    hevc = make_video(tmp_path / "u/b.mp4", 3, "libx265")
    make_file(tmp_path / "u/broken.mp4", os.urandom(4000))
    flat = make_video(tmp_path / "_flat/f.mp4", 2)
    inode = os.stat(avc).st_ino
    hevc_before = read(hevc)

    shrinker = Shrinker(str(tmp_path), LOG)
    assert shrinker.run() == 2                  # broken file, run continues
    probe = MediaProbe()
    assert probe.video_codec(avc) == "hevc"
    assert os.stat(avc).st_ino == inode         # written in place
    assert read(hevc) == hevc_before
    assert probe.video_codec(flat) == "h264"    # _flat skipped
    assert shrinker.stats["shrunk"] == 1
    assert shrinker.stats["hevc"] == 1 and shrinker.stats["failed"] == 1
    assert not os.path.exists(tmp_path / "u/newShrinkedFiles")
    assert not os.path.exists(tmp_path / "_flat" / ShrinkDatabase.NAME)

    second = Shrinker(str(tmp_path), LOG)
    second.run()
    assert second.stats["skipped"] == 2         # database has precedence


@needs_ffmpeg
def test_shrinker_keep_copies(tmp_path: Path) -> None:
    """Review mode leaves originals and the database untouched.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    video = make_video(tmp_path / "a.mp4", 2)
    before = read(video)
    assert Shrinker(str(tmp_path), LOG, keep_copies=True).run() == 0
    assert read(video) == before
    assert os.path.exists(tmp_path / "newShrinkedFiles/a.mp4")
    assert not ShrinkDatabase(str(tmp_path)).rows()


@needs_ffmpeg
def test_encoder_rejects_duration_mismatch(tmp_path: Path) -> None:
    """A truncated output is rejected.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    long_video = make_video(tmp_path / "long.mp4", 6)
    short_video = make_video(tmp_path / "short.mp4", 2)
    assert not HevcEncoder(logger=LOG).validate(long_video, short_video)
    assert HevcEncoder(logger=LOG).validate(long_video, long_video)


def test_encoder_command_has_no_shell() -> None:
    """Special characters are passed as one argument."""
    cmd = HevcEncoder(crf=30, threads=2).command("in $x.mp4", "out.mp4")
    assert cmd[cmd.index("-i") + 1] == "in $x.mp4" and "30" in cmd


# -- ProgressReport ------------------------------------------------------
def test_progress_is_read_only(tmp_path: Path) -> None:
    """The report creates no database and deletes nothing.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    first = make_file(tmp_path / "d/a.mp4", b"1")
    make_file(tmp_path / "d/b.mp4", b"2")
    make_file(tmp_path / "e/c.mp4", b"3")
    make_file(tmp_path / "d/newShrinkedFiles/keep.mp4", b"4")
    database = ShrinkDatabase(str(tmp_path / "d"))
    database.ensure()
    database.record(first)
    report = ProgressReport(str(tmp_path), LOG)
    assert report.run() == 0
    assert report.done == [first] and len(report.todo) == 2
    assert os.path.exists(tmp_path / "d/newShrinkedFiles/keep.mp4")
    assert not os.path.exists(tmp_path / "e" / ShrinkDatabase.NAME)
