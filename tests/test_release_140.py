"""1.4.0: freed space per session and per day; session line in status and GUI."""
import os
import tkinter as tk
from datetime import datetime
from pathlib import Path

import pytest

from test_gui_logic import write_run
from mediatools.cli import Cli
from mediatools.gui.savingspanel import SavingsPanel
from mediatools.gui.shrinkmonitor import ShrinkMonitor
from mediatools.shrink.savings import Savings
from mediatools.shrink.savingslog import SavingsLog
from mediatools.shrink.spacetally import SpaceTally

MB = 1_000_000


def row(stamp: str, number: int, src: int, out: str, status: str = "shrunk") -> str:
    """Build the log lines of one result table row (wrapped like the real one).

    Args:
        stamp: Log time ``YYYY-MM-DD HH:MM:SS``.
        number: Row number.
        src: Source MB.
        out: Output MB text (``-`` for none).
        status: Status column.

    Returns:
        Two log lines.
    """
    out_cell = f"{out:>5} MB" if out != "-" else "       -"
    first = (f"{stamp},123 INFO    │ {number:>4} │ {stamp[11:16]} │ {src:>5} MB │ {out_cell} │ "
             f"12.9 % │ 24.8 min │  8.4 min │ {status:<6} │ some/folder/            │\n")
    return first + f"{stamp},124 INFO    │      │       │          │          │ name.mp4 │\n"


def write_logs(folder: Path) -> None:
    """Write three sessions: two on one day, one over midnight.

    Args:
        folder: Log folder.
    """
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "shrink_2026-11-10_08-00-00.log").write_text(
        "2026-11-10 08:00:00,001 INFO    mediatools 1.2.15 (pid 1)\n"
        + row("2026-11-10 08:30:00", 1, 1000, "200")
        + row("2026-11-10 08:31:00", 2, 300, "-", "HEVC")
        + row("2026-11-10 09:00:00", 3, 2000, "500"), encoding="utf-8")
    (folder / "shrink_2026-11-10_22-00-00.log").write_text(
        row("2026-11-10 23:30:00", 1, 1000, "100") + row("2026-11-11 00:30:00", 2, 1000, "300"),
        encoding="utf-8")
    (folder / "shrink_2026-11-11_10-00-00.log").write_text("no rows\n", encoding="utf-8")
    (folder / "other.log").write_text(row("2026-11-12 01:00:00", 1, 9999, "1"), encoding="utf-8")


def test_sessions_days_and_total(tmp_path: Path) -> None:
    """Rows of status shrunk are summed per log, per day and in total.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    write_logs(tmp_path / "logs")
    logs = SavingsLog(str(tmp_path / "logs"))
    sessions = logs.sessions()
    assert [(s.name, s.files, s.src // MB, s.out // MB) for s in sessions] == [
        ("shrink_2026-11-10_08-00-00", 2, 3000, 700),
        ("shrink_2026-11-10_22-00-00", 2, 2000, 400)]
    assert sessions[0].start == datetime(2026, 11, 10, 8, 0)
    assert sessions[0].end == datetime(2026, 11, 10, 9, 0)
    assert sessions[0].freed == 2300 * MB and round(sessions[0].share, 1) == 23.3
    days = logs.days()
    assert [(d.name, d.files, d.freed // MB) for d in days] == [("2026-11-10", 3, 3200),
                                                                ("2026-11-11", 1, 700)]
    total = logs.total()
    assert total.name == "since 2026-11-10" and total.files == 4 and total.freed == 3900 * MB
    report = logs.report("session")
    assert report[0].startswith("session") and report[1].startswith("2026-11-10 08:00")
    assert report[-2].startswith("total since 2026-11-10")
    assert "freed GiB" in logs.report("day")[0] and logs.report("day")[1].startswith("2026-11-10")
    assert Savings("x", datetime.now(), datetime.now()).share == 0.0


def test_cache_and_missing_logs(tmp_path: Path) -> None:
    """Unchanged logs are not read again; missing folders and files give nothing.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    write_logs(tmp_path / "logs")
    logs = SavingsLog(str(tmp_path / "logs"))
    path = str(tmp_path / "logs" / "shrink_2026-11-10_08-00-00.log")
    first = logs.rows(path)
    assert logs.rows(path) is first
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(row("2026-11-10 10:00:00", 4, 500, "50"))
    assert len(logs.rows(path)) == 3
    assert not logs.rows(str(tmp_path / "gone.log"))
    empty = SavingsLog(str(tmp_path / "none"))
    assert not empty.logs() and empty.total().name == "nothing shrunk yet"
    assert SavingsLog.started("other.log") is None
    assert SavingsLog.gib(3 * 1024 ** 3) == "3.0"


def test_tally_from_logs(tmp_path: Path) -> None:
    """Session = current log, overall = all logs, labelled with dates.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    write_logs(tmp_path / "logs")
    logs = SavingsLog(str(tmp_path / "logs"))
    current = str(tmp_path / "logs" / "shrink_2026-11-10_22-00-00.log")
    tally = SpaceTally.from_logs(logs, current)
    assert tally.session() == (2000 * MB, 400 * MB) and tally.overall() == (5000 * MB, 1100 * MB)
    assert tally.line().startswith("space  session since 2026-11-10 22:00: 1.9 GiB read")
    assert "| since 2026-11-10: 4.7 GiB read" in tally.line()
    tally.add(1000 * MB, 100 * MB)                 # replaced by this process
    assert tally.session() == (3000 * MB, 500 * MB)
    odd = SpaceTally.from_logs(logs, str(tmp_path / "logs" / "other.log"))
    assert odd.started == "" and odd.line().startswith("space  session: ")
    none = SpaceTally.from_logs(SavingsLog(str(tmp_path / "x")), None)
    assert none.line().startswith("space  session: 0.0") and "| overall: " in none.line()


def test_cli_savings(datadir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """``mediatools savings`` prints the table per session or per day.

    Args:
        datadir: Data directory (fixture).
        capsys: Output capture (fixture).
    """
    write_logs(datadir / "logs")
    assert Cli().main(["savings"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("session") and "total since 2026-11-10" in out
    assert Cli().main(["savings", "--by", "day"]) == 0
    assert capsys.readouterr().out.startswith("day")


def test_monitor_shows_session_and_last_session(datadir: Path) -> None:
    """A running shrink shows its session; otherwise the last session.

    Args:
        datadir: Data directory (fixture).
    """
    write_logs(datadir / "logs")
    monitor = ShrinkMonitor()
    assert monitor.read().overall.startswith(
        "space  last session since 2026-11-11 10:00: 0.0 GiB read")
    log = write_run(datadir, "total\nfile\n")
    os.replace(log, datadir / "logs" / "shrink_2026-11-12_04-56-27.log")
    pid_file = Path(monitor.control.pid_file)
    pid_file.write_text(f"{os.getpid()}\n{datadir / 'logs' / 'shrink_2026-11-12_04-56-27.log'}\n",
                        encoding="utf-8")
    assert monitor.read().overall.startswith("space  session since 2026-11-12 04:56: ")


def test_savings_panel(datadir: Path) -> None:
    """The panel lists sessions newest first and switches to days.

    Args:
        datadir: Data directory (fixture).
    """
    assert os.environ.get("DISPLAY"), "needs a display (tools/check.sh uses Xvfb)"
    write_logs(datadir / "logs")
    root = tk.Tk()
    root.withdraw()
    try:
        panel = SavingsPanel(root)
        rows = panel.tree.get_children()
        labels = [panel.tree.item(item, "text") for item in rows]
        assert labels == ["2026-11-10 22:00", "2026-11-10 08:00"]
        assert str(panel.tree.item(rows[1], "values")[2]) == "2"
        assert panel.total.get().startswith("total since 2026-11-10: 4 files")
        panel.by.set("day")
        panel.refresh()
        assert [panel.tree.item(item, "text") for item in panel.tree.get_children()] == [
            "2026-11-11", "2026-11-10"]
    finally:
        root.destroy()
