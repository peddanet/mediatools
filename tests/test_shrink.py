"""Unit tests for mediatools.shrink."""
import logging
import os
import sqlite3
from pathlib import Path

import pytest

from conftest import make_file, make_video, needs_ffmpeg, read
from mediatools.core.probe import MediaProbe
from mediatools.shrink import (HevcEncoder, ProgressReport, ShrinkDatabase, Shrinker,
                               ShrinkOptions, ThroughputLog)

LOG = logging.getLogger("test")


# -- ShrinkDatabase ------------------------------------------------------
def test_database_record_and_lookup(tmp_path: Path) -> None:
    """A recorded name stays processed; a size change is reported as changed.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    path = make_file(tmp_path / "a.mp4", b"0123456789")
    database = ShrinkDatabase(str(tmp_path))
    database.ensure()
    assert not database.is_done(path)
    database.record(path)
    assert database.is_done(path)
    make_file(tmp_path / "a.mp4", b"0123456789a")          # 10 % larger: changed
    assert database.status(path) == "changed" and database.is_done(path)
    make_file(tmp_path / "a.mp4", b"0123456789ab")         # 20 % larger: regrown
    assert ShrinkDatabase(str(tmp_path)).status(path) == "regrown"
    assert ShrinkDatabase(str(tmp_path)).is_done(path)


def test_database_matches_by_name_after_move(tmp_path: Path) -> None:
    """Rows written at an earlier location of the collection still count.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    path = make_file(tmp_path / "new/place/a.mp4", b"abc")
    make_file(tmp_path / "new/place/b.mp4", b"xyz")
    conn = sqlite3.connect(tmp_path / "new/place" / ShrinkDatabase.NAME)
    conn.execute(ShrinkDatabase.SCHEMA)
    conn.execute("INSERT INTO files VALUES (?, ?, ?)",
                 ("/home/u/Desktop/old/place/a.mp4", 3, "2026-01-01 00:00:00"))
    conn.commit()
    conn.close()
    database = ShrinkDatabase(str(tmp_path / "new/place"))
    assert database.status(path) == "done"
    assert database.status(str(tmp_path / "new/place/b.mp4")) == "todo"


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

    shrinker = Shrinker(str(tmp_path), LOG, ShrinkOptions(order="path"))
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
    assert Shrinker(str(tmp_path), LOG, ShrinkOptions(keep_copies=True)).run() == 0
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
    throughput = ThroughputLog(str(tmp_path / "speed.json"))
    throughput.add(10_000_000, 2.0)
    report = ProgressReport(str(tmp_path), LOG, throughput=throughput)
    assert report.run() == 0
    assert report.lists["done"] == [first] and len(report.lists["todo"]) == 2
    assert report.todo_bytes == 2 and report.databases == 1 and report.orphan_rows == 0
    assert os.path.exists(tmp_path / "d/newShrinkedFiles/keep.mp4")
    assert not os.path.exists(tmp_path / "e" / ShrinkDatabase.NAME)


@needs_ffmpeg
def test_encoder_reports_progress(tmp_path: Path) -> None:
    """ffmpeg's progress output reaches the callback; its log is removed.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    video = make_video(tmp_path / "a.mp4", 3)
    seen: list[float] = []
    assert HevcEncoder(logger=LOG).encode(video, str(tmp_path / "out.mp4"), seen.append)
    assert seen and max(seen) <= 1.0
    assert not os.path.exists(tmp_path / "out.mp4.ffmpeg.log")


def test_encoder_keeps_one_cpu_free(monkeypatch: pytest.MonkeyPatch) -> None:
    """Decoder threads and the x265 pool default to logical CPUs minus one.

    Args:
        monkeypatch: Patcher (fixture).
    """
    monkeypatch.setattr(os, "cpu_count", lambda: 16)
    cmd = HevcEncoder().command("in.mp4", "out.mp4")
    assert cmd[cmd.index("-threads") + 1] == "15"
    assert cmd[cmd.index("-x265-params") + 1] == "pools=15"
    monkeypatch.setattr(os, "cpu_count", lambda: None)
    assert HevcEncoder().threads == 1
    assert HevcEncoder(threads=4).command("a", "b")[-2] == "pools=4"
