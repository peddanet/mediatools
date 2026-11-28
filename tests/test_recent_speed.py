"""The remaining time uses the speed of the recent running time (ledger)."""
import io
import logging
import time
from pathlib import Path

import pytest

from mediatools.core.progressbar import ProgressBar
from mediatools.shrink.jobspec import JobSpec
from mediatools.shrink.ledger import Ledger
from mediatools.shrink.ledgerrow import LedgerRow
from mediatools.shrink.recentspeed import RecentSpeed
from mediatools.shrink.shrinker import Shrinker

MB = 1_000_000


def ledger_with(tmp_path: Path, rows: list[tuple[float, float, int, str]]) -> Ledger:
    """Write a ledger with rows ``(end epoch, took, source bytes, status)``.

    Args:
        tmp_path: Temporary directory.
        rows: The rows, oldest first.

    Returns:
        The ledger.
    """
    ledger = Ledger(str(tmp_path / "ledger.tsv"))
    for end, took, source, status in rows:
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(end))
        ledger.add(LedgerRow(when, "s", status, source, source // 3, took, 60.0, None,
                             "/x.mp4"))
    return ledger


def test_pauses_do_not_count(tmp_path: Path) -> None:
    """Ten hours without encoding between two files change nothing.

    Args:
        tmp_path: Temporary directory.
    """
    start = 1_800_000_000.0
    rows = [(start + 100.0 * i, 100.0, 100 * MB, "shrunk") for i in range(1, 6)]
    rows += [(start + 36_600.0 + 100.0 * i, 100.0, 100 * MB, "shrunk") for i in range(1, 6)]
    speed = RecentSpeed(ledger_with(tmp_path, rows)).rate()
    assert speed is not None and abs(speed - MB) < 1          # 1000 MB in 1000 s running


def test_parallel_encodes_count_once_and_the_window_ends(tmp_path: Path) -> None:
    """Two encodes at a time double the speed; old rows outside the window are ignored.

    Args:
        tmp_path: Temporary directory.
    """
    start = 1_800_000_000.0
    old = [(start + 10.0 * i, 10.0, 1 * MB, "shrunk") for i in range(1, 6)]   # slow, old
    recent = []
    for i in range(1, 121):                                 # 2 x 120 files, 4 h running
        end = start + 100_000.0 + 120.0 * i
        recent += [(end, 120.0, 120 * MB, "shrunk"), (end, 120.0, 120 * MB, "shrunk")]
    speed = RecentSpeed(ledger_with(tmp_path, old + recent)).rate()
    assert speed is not None and abs(speed - 2 * MB) < 1


def test_too_few_files_and_unusable_rows(tmp_path: Path) -> None:
    """Fewer than five usable rows give no speed; FAILED rows and bad dates are skipped.

    Args:
        tmp_path: Temporary directory.
    """
    start = 1_800_000_000.0
    rows = [(start + 100.0 * i, 100.0, 100 * MB, "shrunk") for i in range(1, 5)]
    rows.append((start + 600.0, 100.0, 100 * MB, "FAILED"))
    recent = RecentSpeed(ledger_with(tmp_path, rows))
    assert recent.rate() is None and recent.rate() is None          # cached
    assert RecentSpeed.span(LedgerRow("bad", "s", "shrunk", 1, 1, 1.0, None, None, "/x")) \
        is None
    assert RecentSpeed.busy([]) == 0.0


def test_progress_bar_prefers_the_recent_speed() -> None:
    """With a recent speed the ETA follows it; without one the old average is used."""
    display = ProgressBar(10 * 3600 * MB, 10, initial_rate=4 * MB, stream=io.StringIO())
    assert display.rate() == 4 * MB
    display.base.recent = lambda: 2 * MB
    assert display.remaining_seconds() == 5 * 3600
    display.base.recent = lambda: None
    assert display.rate() == 4 * MB


def test_only_rows_of_the_running_job_count(tmp_path: Path) -> None:
    """Rows of another job in the window do not change the speed of this job.

    Args:
        tmp_path: Temporary directory.
    """
    ledger = Ledger(str(tmp_path / "ledger.tsv"))
    start = 1_800_000_000.0
    for i in range(1, 61):                     # other job, slow: 10 MB in 100 s
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(start + 100.0 * i))
        ledger.add(LedgerRow(when, "a", "shrunk", 10 * MB, MB, 100.0, 60.0, None, "/other/v.mp4"))
    for i in range(1, 11):                     # this job, fast: 300 MB in 100 s
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(start + 6_000.0 + 100.0 * i))
        ledger.add(LedgerRow(when, "b", "shrunk", 300 * MB, MB, 100.0, 60.0, None, "/this/v.mp4"))
    mixed = RecentSpeed(ledger).rate()
    own = RecentSpeed(ledger, lambda row: row.path.startswith("/this/")).rate()
    assert mixed is not None and mixed < 2 * MB                  # slow rows drag it down
    assert own is not None and abs(own - 3 * MB) < 1             # 3000 MB in 1000 s


def test_rows_looked_at_are_counted_per_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """MAX_ROWS limits the rows of the job, not the rows of the whole ledger.

    Args:
        tmp_path: Temporary directory.
        monkeypatch: Sets a small limit.
    """
    ledger = Ledger(str(tmp_path / "ledger.tsv"))
    start = 1_800_000_000.0
    for i in range(1, 8):
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(start + 100.0 * i))
        ledger.add(LedgerRow(when, "b", "shrunk", 100 * MB, MB, 100.0, 60.0, None, "/this/v.mp4"))
    for i in range(1, 11):                     # newer rows of another job
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(start + 1_000.0 + 10.0 * i))
        ledger.add(LedgerRow(when, "a", "shrunk", MB, MB, 10.0, 60.0, None, "/other/v.mp4"))
    monkeypatch.setattr(RecentSpeed, "MAX_ROWS", 6)        # 6 rows of the job
    speed = RecentSpeed(ledger, lambda row: row.session == "b").rate()
    assert speed is not None and abs(speed - MB) < 1


def test_the_shrinker_counts_its_job_else_its_session(tmp_path: Path) -> None:
    """With a job the job's set decides; without one the session; without both all rows.

    Args:
        tmp_path: Temporary directory.
    """
    shrinker = Shrinker(str(tmp_path), logging.getLogger("test"))
    assert shrinker._own_rows() is None                     # pylint: disable=protected-access
    row = LedgerRow("2026-01-01 00:00:00", "s1", "shrunk", 20 * MB, MB, 10.0, 60.0, None,
                    str(tmp_path / "a" / "v.mp4"))
    shrinker.state["session"] = "s1"
    own = shrinker._own_rows()                              # pylint: disable=protected-access
    assert own is not None and own(row) and not own(LedgerRow(
        row.when, "s2", row.status, row.source, row.result, row.took, row.length, row.mtime,
        row.path))
    shrinker.state["job"] = JobSpec(str(tmp_path / "a"), min_size=10 * MB)
    own = shrinker._own_rows()                              # pylint: disable=protected-access
    assert own is not None and own(row)
    assert not own(LedgerRow(row.when, "s1", row.status, 5 * MB, MB, 10.0, 60.0, None, row.path))
