"""The remaining time uses the speed of the recent running time (ledger)."""
import io
import time
from pathlib import Path

from mediatools.core.progressbar import ProgressBar
from mediatools.shrink.ledger import Ledger
from mediatools.shrink.ledgerrow import LedgerRow
from mediatools.shrink.recentspeed import RecentSpeed

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
