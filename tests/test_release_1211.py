"""1.2.11: originals restored over their shrunk version are encoded again."""
import logging
from pathlib import Path

import pytest

from conftest import make_file
from test_shrink_run import FakeProbe, build, videos
from mediatools.shrink import ProgressReport, ShrinkDatabase
from mediatools.shrink.throughput import ThroughputLog

LOG = logging.getLogger("test")


class HevcProbe(FakeProbe):
    """Probe double: every file is already HEVC."""

    def video_codec(self, _path: str) -> str:
        """Report HEVC for every file.

        Returns:
            Always ``hevc``.
        """
        return "hevc"


def shrink_all_then_restore(tmp_path: Path) -> dict[str, str]:
    """Shrink the three test videos, then put the large original back.

    Args:
        tmp_path: Temporary directory.

    Returns:
        Path per file name.
    """
    paths = videos(tmp_path)
    shrinker, _encoder = build(tmp_path)
    assert shrinker.run() == 0
    make_file(Path(paths["large.mp4"]), b"l" * 1000)           # restored original
    return paths


def test_restored_original_is_encoded_again(tmp_path: Path,
                                            caplog: pytest.LogCaptureFixture) -> None:
    """A recorded video that is clearly larger again and not HEVC is planned.

    Args:
        tmp_path: Temporary directory (fixture).
        caplog: Log capture (fixture).
    """
    paths = shrink_all_then_restore(tmp_path)
    again, encoder = build(tmp_path)
    with caplog.at_level(logging.INFO, logger="test"):
        assert again.run() == 0
    assert encoder.calls == ["large.mp4"] and again.stats["regrown"] == 1
    assert again.stats["skipped"] == 2
    assert "larger again" in caplog.text
    assert ShrinkDatabase(str(tmp_path / "c/b")).status(paths["large.mp4"]) == "done"


def test_regrown_hevc_file_is_left_alone(tmp_path: Path) -> None:
    """A larger file that is already HEVC counts as processed.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    shrink_all_then_restore(tmp_path)
    again, encoder = build(tmp_path, probe=HevcProbe())
    assert again.run() == 0 and not encoder.calls
    assert again.stats["regrown"] == 0 and again.stats["skipped"] == 3


def test_progress_counts_regrown_as_work(tmp_path: Path) -> None:
    """progress lists regrown videos separately and adds them to the bytes to do.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    paths = shrink_all_then_restore(tmp_path)
    report = ProgressReport(str(tmp_path / "c"), LOG,
                            throughput=ThroughputLog(str(tmp_path / "t.json")))
    assert report.run() == 0
    assert report.lists["regrown"] == [paths["large.mp4"]]
    assert report.todo_bytes == 1000 and not report.lists["todo"]
    written = report.write()
    assert Path(written["regrown"]).read_text(encoding="utf-8").strip() == paths["large.mp4"]
