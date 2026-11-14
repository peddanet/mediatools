"""Release 1.6.2: scrolling while output grows, jobs from old logs, Run offers the jobs."""
import tkinter as tk
from pathlib import Path

import pytest

from test_gui_tk import fixture_root  # pylint: disable=unused-import
from mediatools.cli import Cli
from mediatools.gui.app import MediaToolsApp
from mediatools.gui.catalog import CommandCatalog
from mediatools.gui.consoleview import ConsoleView
from mediatools.gui.formstore import FormStore
from mediatools.gui.jobpicker import JobPicker
from mediatools.gui.shrinkmonitor import ShrinkMonitor
from mediatools.gui.shrinkpanel import ShrinkPanel
from mediatools.shrink.encoder import HevcEncoder
from mediatools.shrink.jobregistry import JobEntry, JobRegistry
from mediatools.shrink.jobspec import JobSpec


def lines(count: int) -> str:
    """Return numbered text lines.

    Args:
        count: Number of lines.

    Returns:
        ``line 0`` to ``line count-1``, one per line.
    """
    return "\n".join(f"line {number}" for number in range(count))


def test_console_keeps_a_scrolled_view(root: tk.Tk) -> None:
    """New output follows only while the end is shown.

    Args:
        root: Main window (fixture).
    """
    root.deiconify()
    console = ConsoleView(root)
    console.frame.pack()
    root.update()
    console.write(lines(300) + "\n")
    root.update()
    assert console.text.yview()[1] >= 0.999
    console.text.yview_moveto(0.2)
    root.update()
    console.write("more\n")
    root.update()
    assert console.text.yview()[0] == pytest.approx(0.2, abs=0.02)


def test_shrink_log_keeps_its_position(root: tk.Tk) -> None:
    """The log tail is redrawn without jumping back to the start.

    Args:
        root: Main window (fixture).
    """
    root.deiconify()
    panel = ShrinkPanel(root, refresh_ms=0)
    panel.frame.pack()
    root.update()
    panel.show_log(lines(300))
    root.update()
    assert panel.log.yview()[1] >= 0.999                        # starts at the end
    panel.log.yview_moveto(0.5)
    root.update()
    panel.show_log(lines(301))
    root.update()
    first = panel.log.yview()[0]
    assert 0.4 < first < 0.6
    panel.show_log(lines(301))                                  # unchanged: untouched
    assert panel.log.yview()[0] == first


def write_log(folder: Path, name: str, text: str) -> None:
    """Write a shrink log.

    Args:
        folder: Log folder.
        name: File name.
        text: Content.
    """
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(text, encoding="utf-8")


def test_jobs_are_taken_from_old_logs(tmp_path: Path) -> None:
    """Root lines and job lines of old logs become jobs, once.

    Args:
        tmp_path: Temporary directory.
    """
    logs = tmp_path / "logs"
    write_log(logs, "shrink_2026-11-09_08-00-00.log",
              "2026-11-09 08:00:00,1 INFO    shrink root: /m/c  mode: replace originals  "
              "order: largest\n")
    write_log(logs, "shrink_2026-11-13_22-00-00.log",
              "2026-11-13 22:00:00,1 INFO    shrink root: /m/c/a  mode: replace  order: path\n"
              '2026-11-13 22:00:00,2 INFO    shrink job: {"crf": 26, "ext": [".mp4"], '
              '"jobs": 2, "min_seconds": 0.0, "min_size": 300000000, "newer_than": null, '
              '"order": "path", "preset": "faster", "root": "/m/c/a"}\n')
    write_log(logs, "shrink_2026-11-10_08-00-00.log", "no start line\n")
    registry = JobRegistry(str(tmp_path / "jobs.json"), logs=str(logs))
    entries = registry.entries()
    assert [entry.spec.root for entry in entries] == ["/m/c/a", "/m/c"]
    newest, oldest = entries[0], entries[1]
    assert newest.spec.min_size == 300_000_000 and newest.settings["preset"] == "faster"
    assert newest.argv() == ["shrink", "/m/c/a", "--crf", "26", "--preset", "faster",
                             "--order", "path", "--jobs", "2", "--min-size", "300M"]
    threaded = JobEntry(JobSpec("/m/c"), {"jobs": 2, "threads": 11})
    assert threaded.argv() == ["shrink", "/m/c", "--jobs", "2", "--threads", "11"]
    assert not oldest.planned and oldest.progress() == "not planned yet"
    assert oldest.gauge(4) == "????" and "to go: ?" in JobRegistry.line(oldest)
    assert registry.backfill() == 0                              # only once
    registry.finished_file(oldest.spec, 100)                     # unknown remainder stays
    registry.planned(oldest.spec, {"crf": 28}, 5000, 5)
    planned = [entry for entry in registry.entries() if entry.spec.root == "/m/c"][0]
    assert planned.planned and planned.todo_bytes == 5000


def test_run_offers_the_jobs_first(root: tk.Tk, tmp_path: Path, datadir: Path) -> None:
    """Run in the shrink tab opens the dialog; its buttons start the choice.

    Args:
        root: Main window (fixture).
        tmp_path: Temporary directory (fixture).
        datadir: Data directory (fixture).
    """
    del datadir
    app = MediaToolsApp(root, CommandCatalog(Cli().parser), FormStore(str(tmp_path / "g.json")),
                        ask=lambda _t, _x: True, refresh_ms=0)
    form = app.forms["shrink"]
    started: list[str] = []
    form.console.run = lambda args, shown: started.append(shown) or True
    assert form.run() and len(started) == 1                      # no jobs: runs at once
    ShrinkMonitor().registry().planned(JobSpec("/m/c"), {"crf": 24}, 10, 1)
    assert not form.run() and len(started) == 1                  # dialog is open
    dialogs = [child for child in root.winfo_children() if isinstance(child, tk.Toplevel)]
    assert len(dialogs) == 1
    assert app.run_choice(None) and len(started) == 2
    assert app.run_choice(JobEntry(JobSpec("/m/c"), {"crf": 24, "jobs": 2, "threads": 11}))
    assert "--crf 24" in started[-1] and "--threads 11" in started[-1]
    chosen: list[object] = []
    picker = JobPicker(root, ShrinkMonitor(), "mediatools shrink", chosen.append)
    rows = picker.jobs.jobs.get_children()
    picker.jobs.jobs.selection_set(rows[0])
    root.update()
    assert not chosen                                            # selecting alone starts nothing
    picker.jobs.pick(True)
    assert isinstance(chosen[0], JobEntry) and not picker.window.winfo_exists()


def test_explicit_threads_are_remembered() -> None:
    """Only a requested thread count is a job setting; the default is not."""
    assert HevcEncoder(threads=11).threads_given == 11
    assert HevcEncoder().threads_given is None and HevcEncoder().threads >= 1
