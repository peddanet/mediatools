"""Result table of a shrink run and the systemd sleep inhibitor."""
import logging
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from test_shrink_run import FakeEncoder, build, videos
from mediatools.core.sleepinhibitor import SleepInhibitor
from mediatools.shrink.fileresult import FileResult
from mediatools.shrink.resulttable import ResultTable


class FailingEncoder(FakeEncoder):
    """Encoder double that fails on one file name."""

    def encode(self, src: str, dst: str,
               on_progress: Callable[[float], None] | None = None) -> bool:
        """Fail for ``medium.mp4``, else behave like the base double.

        Args:
            src: Input file.
            dst: Output file.
            on_progress: Progress callback.

        Returns:
            False for ``medium.mp4``.
        """
        if os.path.basename(src) == "medium.mp4":
            self.calls.append("medium.mp4")
            return False
        return super().encode(src, dst, on_progress)


def test_row_layout_and_wrapping(tmp_path: Path) -> None:
    """Figures sit in fixed columns; a long path wraps inside its cell.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    table = ResultTable(str(tmp_path), str(tmp_path / "r.txt"), logging.getLogger("t"))
    cells = table.cells(7, FileResult(str(tmp_path / "x.mp4"), "shrunk",
                                      2_860_000_000, 509_618_510, 2659.5, 1680.0))
    assert cells[0] == "7"
    assert cells[2:] == ["2860 MB", "510 MB", "17.8 %", "44.3 min", "28.0 min", "shrunk"]
    assert table.cells(8, FileResult("y", "FAILED", 1_000_000))[3:6] == ["-", "-", "-"]
    lines = table.row(cells, "dir one/" + "word " * 20 + "end.mp4", 110)
    assert len(lines) > 1 and {len(line) for line in lines} == {110}
    assert "2860 MB" in lines[0] and "2860 MB" not in lines[1]
    head = table.head(60)                       # narrower than possible
    assert [len(line) for line in head] == [90, 90, 90] and "│ file" in head[1]
    assert ResultTable.wrap("a/b/clip.mp4", 20) == ["a/b/clip.mp4"]
    assert ResultTable.wrap("folder name/x.mp4", 12) == ["folder name/", "x.mp4"]
    assert ResultTable.wrap("a" * 12, 5) == ["aaaaa", "aaaaa", "aa"]
    assert ResultTable.wrap("", 5) == [""]


def test_shrink_run_writes_result_table(tmp_path: Path,
                                        caplog: pytest.LogCaptureFixture) -> None:
    """Every finished file becomes one framed row in the log and the report.

    Args:
        tmp_path: Temporary directory (fixture).
        caplog: Log capture (fixture).
    """
    videos(tmp_path)
    shrinker, _encoder = build(tmp_path, encoder=FailingEncoder())
    report = tmp_path / "report.txt"
    shrinker.state["results"] = ResultTable(shrinker.root, str(report), shrinker.log, width=110)
    caplog.set_level(logging.INFO, logger="test")
    assert shrinker.run() == 2                          # one file failed
    lines = report.read_text(encoding="utf-8").splitlines()
    assert lines[0] == f"shrink root: {shrinker.root}"
    assert lines[1].startswith("┌") and lines[-1].startswith("└")
    rows = [line for line in lines if line.startswith("│") and "MB" in line]
    assert [row.split("│")[8].strip() + " " + row.split("│")[9].strip() for row in rows] == [
        "shrunk b/large.mp4", "FAILED a/medium.mp4", "shrunk a/small.mp4"]
    assert {len(line) for line in lines[1:]} == {ResultTable.REPORT_WIDTH}
    shown = [r.getMessage() for r in caplog.records if r.getMessage()[:1] in "┌│├└"]
    assert shown and {len(line) for line in shown} == {110}
    assert "results (also in" in caplog.text
    assert "DB record" not in caplog.text            # details are DEBUG (log file only)


def test_no_frame_without_rows(tmp_path: Path) -> None:
    """finish() without rows writes nothing.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    table = ResultTable(str(tmp_path), str(tmp_path / "r.txt"), logging.getLogger("t"))
    table.finish()
    assert not (tmp_path / "r.txt").exists()


def fake_inhibit(tmp_path: Path, body: str) -> str:
    """Write a stand-in for systemd-inhibit.

    Args:
        tmp_path: Temporary directory.
        body: Shell code after the shebang.

    Returns:
        Path of the executable script.
    """
    script = tmp_path / "fake-inhibit"
    script.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    script.chmod(0o755)
    return str(script)


def test_inhibitor_holds_and_releases(tmp_path: Path) -> None:
    """The helper runs while the lock is held and is gone after stop().

    Args:
        tmp_path: Temporary directory (fixture).
    """
    path = fake_inhibit(tmp_path, 'while [ "${1#--}" != "$1" ]; do shift; done\nexec "$@"\n')
    lock = SleepInhibitor("test", logging.getLogger("t"), inhibit=path)
    assert lock.start()
    assert lock.proc is not None and lock.proc.poll() is None
    proc = lock.proc
    lock.stop()
    assert lock.proc is None and proc.poll() is not None
    lock.stop()                                        # second stop is harmless


def test_inhibitor_refused_or_missing(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A refused or missing systemd-inhibit only produces a warning.

    Args:
        tmp_path: Temporary directory (fixture).
        caplog: Log capture (fixture).
    """
    log = logging.getLogger("t")
    script = fake_inhibit(tmp_path, "echo denied >&2\nexit 1\n")
    refused = SleepInhibitor("test", log, inhibit=script)
    assert not refused.start() and refused.proc is None
    assert "refused the lock (denied)" in caplog.text
    missing = SleepInhibitor("test", log, inhibit=str(tmp_path / "nothing-here"))
    assert not missing.start()
    assert "not found" in caplog.text
