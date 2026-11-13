"""1.4.2: the first progress line counts read and to-go source bytes of the whole job."""
import io
from pathlib import Path

from test_release_140 import row, write_logs
from test_shrink_run import build, videos
from mediatools.core.progressbar import ProgressBar
from mediatools.core.progressbase import ProgressBase
from mediatools.shrink.savingslog import SavingsLog

GIB = 1024 ** 3


def test_line_counts_job_read_and_to_go() -> None:
    """Read includes all runs before this process; to go is this plan."""
    pbar = ProgressBar(8 * GIB, 4, stream=io.StringIO())
    pbar.base = ProgressBase(2 * GIB, 3, "2026-11-09", lambda: GIB // 2)
    pbar.file_done(2 * GIB)
    line = pbar.line("x", 0.5, 2 * GIB)
    assert line.startswith("job   [")
    assert ("| since 2026-11-09: 5.0 GiB read (-> 0.5 GiB after shrinking), "
            "5.0 GiB to go |") in line
    assert " 50.0 % | ETA " in line and line.endswith("4 files done, 3 to go")
    pbar.file_done(6 * GIB)                        # more than planned: never negative
    assert "0.0 GiB to go" in pbar.line() and "100.0 %" in pbar.line()
    plain = ProgressBar(GIB, 1, stream=io.StringIO())
    assert "| ETA ? | 0.0 GiB read, 1.0 GiB to go |" in plain.line()


def test_status_file_properties(tmp_path: Path) -> None:
    """``status_path`` and ``last_status`` keep working as before.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    pbar = ProgressBar(GIB, 1, stream=io.StringIO())
    assert pbar.status_path is None and pbar.last_status == 0.0
    pbar.status_path = str(tmp_path / "s")
    pbar.last_status = 5.0
    assert pbar.status.path == str(tmp_path / "s") and pbar.status.last == 5.0


def test_read_counts_every_encoded_file(tmp_path: Path) -> None:
    """Shrunk, copies and failures count as read; skipped files do not.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    write_logs(tmp_path / "logs")
    path = tmp_path / "logs" / "shrink_2026-11-10_08-00-00.log"
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(row("2026-11-10 10:00:00", 4, 700, "-", "FAILED"))
        handle.write(row("2026-11-10 10:05:00", 5, 400, "90", "copy"))
    logs = SavingsLog(str(tmp_path / "logs"))
    assert logs.read(str(path)) == ((1000 + 2000 + 700 + 400) * 1_000_000, 4)
    assert logs.read(str(tmp_path / "missing.log")) == (0, 0)
    assert logs.read_all() == ((4100 + 2000) * 1_000_000, 6, "2026-11-10")
    assert SavingsLog(str(tmp_path / "none")).read_all() == (0, 0, "")


def test_shrink_run_uses_progress_base(tmp_path: Path) -> None:
    """The run hands its base, with the size after shrinking, to the bar.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    videos(tmp_path)
    shrinker, _encoder = build(tmp_path)
    shrinker.state["base"] = ProgressBase(GIB, 7, "2026-11-09")
    assert shrinker.run() == 0
    assert shrinker.display is not None and shrinker.display.base.files == 7
    after = shrinker.display.base.after
    assert after is not None and after() == shrinker.state["space"].overall()[1]
    assert "since 2026-11-09: 1.0 GiB read (-> 0.0 GiB after shrinking)" in \
        shrinker.display.line()
