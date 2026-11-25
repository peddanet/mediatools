"""Release 1.13.1: queue in the shrink tab, job dialog, removable jobs, resizable parts."""
import json
import os
import tkinter as tk
from pathlib import Path
from tkinter import ttk

import pytest

from test_gui_logic import write_run
from test_gui_tk import fixture_root  # noqa: F401  pylint: disable=unused-import
from mediatools.cli import Cli
from mediatools.gui.app import MediaToolsApp
from mediatools.gui.catalog import CommandCatalog
from mediatools.gui.consoleview import ConsoleView
from mediatools.gui.formstore import FormStore
from mediatools.gui.jobdialog import JobDialog
from mediatools.gui.joblist import JobList
from mediatools.gui.queuelist import QueueList
from mediatools.gui.shrinkmonitor import ShrinkMonitor
from mediatools.gui.shrinkpanel import ShrinkPanel
from mediatools.shrink.jobregistry import JobEntry, JobRegistry
from mediatools.shrink.jobspec import JobSpec
from mediatools.shrink.queuecommand import QueueCommand
from mediatools.shrink.shrinkqueue import ShrinkQueue


def parse(args: list[str]) -> object:
    """Parse shrink arguments like the GUI does.

    Args:
        args: Arguments, folder first.

    Returns:
        The namespace.
    """
    return Cli().parser.parse_args(["shrink", *args])


# -- registry and queue ------------------------------------------------------------------
def test_registry_remove_and_queue_replace(tmp_path: Path) -> None:
    """Jobs can be forgotten; a waiting job can be changed in its place.

    Args:
        tmp_path: Temporary directory.
    """
    registry = JobRegistry(str(tmp_path / "jobs.json"))
    one, two = JobSpec(str(tmp_path / "a")), JobSpec(str(tmp_path / "b"))
    registry.planned(one, {}, 1, 1)
    registry.planned(two, {}, 1, 1)
    assert registry.remove([one.key, "unknown", JobRegistry.META]) == 1
    assert registry.remove(["unknown"]) == 0
    assert [entry.spec.key for entry in registry.entries()] == [two.key]
    queue = ShrinkQueue(str(tmp_path / "q.json"))
    queue.add(["/a"], "note")
    queue.replace(1, ["/b", "--jobs", "2"])
    queue.replace(1, ["/c"], "new")
    assert queue.entries()[0]["args"] == ["/c"] and queue.entries()[0]["note"] == "new"
    with pytest.raises(ValueError):
        queue.replace(2, ["/x"])


def test_queue_add_drops_foreground_and_yes(tmp_path: Path) -> None:
    """A queued job never carries --foreground or -y.

    Args:
        tmp_path: Temporary directory.
    """
    command = QueueCommand(ShrinkQueue(str(tmp_path / "q.json")), parse, lambda _a: 0)
    assert command.checked(f"{tmp_path} --foreground -y --jobs 2 --yes") == [
        str(tmp_path), "--jobs", "2"]


# -- the queue list of the shrink tab ---------------------------------------------------
def test_queue_list_rows_and_buttons(root: tk.Tk, tmp_path: Path,
                                     datadir: Path) -> None:
    """Running job first, then the waiting ones; the buttons change the queue.

    Args:
        root: Main window (fixture).
        tmp_path: Temporary directory.
        datadir: Data directory (fixture).
    """
    queue = ShrinkQueue(str(tmp_path / "q.json"))
    started: list[list[str]] = []
    opened: list[tuple] = []
    answers = [False, True]
    view = QueueList(root, ShrinkMonitor(), parse, lambda *args: opened.append(args),
                     queue=queue, confirm=lambda _t, _x: answers.pop(0),
                     launcher=started.append)
    view.refresh()
    assert not view.jobs.get_children()
    assert not view.start() and "queue is empty" in view.message.get()
    folder = tmp_path / "v"
    folder.mkdir()
    ShrinkMonitor().registry().planned(JobSpec(str(folder)), {"crf": 24}, 1024 ** 3, 3)
    queue.add([str(folder), "--preset", "faster", "--jobs", "2", "--threads", "11"], "a")
    queue.add([str(folder), "--min-seconds", "120"])
    queue.add(["--nonsense"])
    view.refresh()
    rows = [view.jobs.item(item, "values") for item in view.jobs.get_children()]
    assert [row[0] for row in rows] == ["1", "2", "3"]
    assert "0.0 / 1.0 GiB" in rows[0][2] and "faster, 2 jobs x 11 threads" in rows[0][3]
    assert rows[1][1] == "new" and rows[2][1] == "?"
    view.jobs.selection_set("2")
    view.shift(-1)
    assert queue.entries()[0]["args"][1:] == ["--min-seconds", "120"]
    view.shift(-1)                                      # already first: nothing happens
    view.jobs.selection_set(("1", "2"))
    view.shift(1)
    assert "select one" in view.message.get()
    assert view.change() is None and "select one waiting job" in view.message.get()
    view.jobs.selection_set("2")
    view.change()
    view.add()
    assert opened[0][0] == 2 and opened[1] == (None, None)
    view.jobs.selection_set("3")
    assert view.remove() == 0 and len(queue.entries()) == 3          # answered no
    assert view.remove() == 1 and len(queue.entries()) == 2
    view.jobs.selection_set("2")
    assert view.start() and started == [["queue", "--run"]]
    assert queue.entries()[0]["args"][1:5] == ["--preset", "faster", "--jobs", "2"]
    view.jobs.selection_remove(*view.jobs.selection())
    assert view.remove() == 0 and "select the waiting" in view.message.get()
    write_run(datadir, None)
    log = Path(ShrinkMonitor().control.active_log() or "")
    log.write_text("x shrink job: " + json.dumps(JobSpec(str(folder)).as_dict()) + "\n",
                   encoding="utf-8")
    view.reload()
    assert view.jobs.get_children()[0] == "running"
    view.jobs.selection_set("running")
    assert view.remove() == 0 and "Stop" in view.message.get()
    assert not view.start() and "a shrink runs" in view.message.get()
    assert JobList.settings_text({}) == "crf 28"


def test_shrink_panel_parts_in_panes(root: tk.Tk, datadir: Path) -> None:
    """In a paned window the three parts get their own sashes.

    Args:
        root: Main window (fixture).
        datadir: Data directory (fixture).
    """
    del datadir
    panes = ttk.PanedWindow(root, orient="vertical")
    panel = ShrinkPanel(panes, ShrinkMonitor(), refresh_ms=0)
    for part, weight in panel.parts:
        panes.add(part, weight=weight)
    assert len(panes.panes()) == 3
    with pytest.raises(ValueError):
        ShrinkPanel.unparsed(["x"])


# -- the job dialog ------------------------------------------------------------------
def test_job_dialog_add_change_and_pick(root: tk.Tk, tmp_path: Path,
                                        datadir: Path) -> None:
    """The dialog fills the form, adds to the queue and changes a waiting job.

    Args:
        root: Main window (fixture).
        tmp_path: Temporary directory.
        datadir: Data directory (fixture).
    """
    del datadir
    spec = {s.name: s for s in CommandCatalog(Cli().parser).commands()}["shrink"]
    queue = ShrinkQueue(str(tmp_path / "q.json"))
    changed: list[int] = []
    dialog = JobDialog(root, spec, ConsoleView(root), parse, queue=queue,
                       on_change=lambda: changed.append(1), ask=lambda _t, _x: True)
    folder = tmp_path / "v"
    folder.mkdir()
    dialog.open()
    assert dialog.mode.get().startswith("New job")
    assert not dialog.enqueue() and "not taken" in dialog.status.get()     # no folder
    dialog.fill([str(folder), "--min-seconds", "120", "--retry-failed", "--bogus", "x",
                 "--jobs"])
    assert dialog.form.values()["retry_failed"] is True
    assert dialog.form.enqueue() and queue.entries()[0]["args"][0] == str(folder)
    assert "--retry-failed" in queue.entries()[0]["args"] and changed == [1]
    assert dialog.enqueue(first=True) and len(queue.entries()) == 2
    assert not dialog.save_changes()                         # not in change mode
    dialog.open(2, [str(folder), "--jobs", "2"])
    assert dialog.mode.get() == "Change waiting job 2"
    assert dialog.form.values()["jobs"] == "2" and dialog.save_changes()
    saved = queue.entries()[1]["args"]
    assert saved[saved.index("--jobs") + 1] == "2"
    dialog.position = 9
    assert not dialog.save_changes() and "not saved" in dialog.status.get()
    dialog.form.set_value("path", "")
    assert not dialog.save_changes()
    dialog.picked(JobEntry(JobSpec(str(folder), min_seconds=300.0), {"crf": 24}), True)
    assert queue.entries()[-1]["args"][queue.entries()[-1]["args"].index("--crf") + 1] == "24"
    dialog.window.withdraw()


def test_job_list_removes_jobs_but_not_the_running_one(root: tk.Tk,
                                                       datadir: Path) -> None:
    """Remove and Remove finished ask first and keep the running job.

    Args:
        root: Main window (fixture).
        datadir: Data directory (fixture).
    """
    registry = ShrinkMonitor().registry()
    done, open_, running = JobSpec("/m/done"), JobSpec("/m/open"), JobSpec("/m/run")
    registry.planned(done, {}, 0, 0)
    registry.planned(open_, {}, 5, 1)
    registry.planned(running, {}, 0, 0)
    answers = [False, True, True, True]
    jobs = JobList(root, ShrinkMonitor(), confirm=lambda _t, _x: answers.pop(0), pack=False)
    jobs.refresh()
    assert len(jobs.jobs.get_children()) == 3
    assert jobs.remove_finished() == 0                       # answered no
    write_run(datadir, None)
    log = Path(ShrinkMonitor().control.active_log() or "")
    log.write_text("x shrink job: " + json.dumps(running.as_dict()) + "\n", encoding="utf-8")
    assert jobs.remove_finished() == 1 and "running job stays" in jobs.message.get()
    jobs.jobs.selection_set(jobs.jobs.get_children())
    assert jobs.remove_selected() == 1
    jobs.jobs.selection_set(jobs.jobs.get_children())
    assert jobs.remove_selected() == 0 and jobs.message.get() == "the running job stays"
    jobs.jobs.selection_remove(*jobs.jobs.selection())
    assert jobs.remove_selected() == 0 and jobs.message.get() == "nothing to remove"
    os.remove(ShrinkMonitor().control.pid_file)
    assert jobs.running_key() is None


def test_app_shrink_tab_has_queue_and_dialog(root: tk.Tk, tmp_path: Path,
                                             datadir: Path) -> None:
    """The shrink tab shows the queue; Add and Change open the job dialog.

    Args:
        root: Main window (fixture).
        tmp_path: Temporary directory.
        datadir: Data directory (fixture).
    """
    del datadir
    app = MediaToolsApp(root, CommandCatalog(Cli().parser),
                        FormStore(str(tmp_path / "g.json")), ask=lambda _t, _x: True,
                        refresh_ms=0)
    assert app.forms["shrink"] is app.jobdialog.form
    app.open_job_dialog(None, None)
    assert app.jobdialog.window.winfo_ismapped() or app.jobdialog.window.state() != "withdrawn"
    assert app.parse_shrink([str(tmp_path)]).path == str(tmp_path)


def test_shrink_tab_default_division(root: tk.Tk, tmp_path: Path, datadir: Path) -> None:
    """Status with its buttons fully visible, the queue, the console; the log gets the rest.

    Args:
        root: Main window (fixture).
        tmp_path: Temporary directory.
        datadir: Data directory (fixture).
    """
    del datadir
    app = MediaToolsApp(root, CommandCatalog(Cli().parser),
                        FormStore(str(tmp_path / "g.json")), ask=lambda _t, _x: True,
                        refresh_ms=0)
    root.geometry("1280x900")
    root.deiconify()
    for _round in range(5):
        root.update()
    panes = app.tabs["shrink"].winfo_children()[0]
    assert isinstance(panes, ttk.PanedWindow)
    status, _queue, log, console = (root.nametowidget(name) for name in panes.panes())
    assert panes.sashpos(0) >= status.winfo_reqheight() - 1        # buttons not cut
    assert log.winfo_height() > console.winfo_height() > 0
    app.zoom(2)
    for _round in range(5):
        root.update()
    assert panes.sashpos(0) >= status.winfo_reqheight() - 1
    ttk.Label(status, text="\n".join(f"line {number}" for number in range(12))).pack()
    status.event_generate("<<StatusHeight>>")                        # as refresh() does
    for _round in range(5):
        root.update()
    assert panes.sashpos(0) >= status.winfo_reqheight() - 1          # grown with it
    root.geometry("1280x200")
    root.update()
    panel = ShrinkPanel(ttk.PanedWindow(root), refresh_ms=0)
    assert not app.layout_shrink(panes, panel)                       # too small: unchanged
    MediaToolsApp.fit_status(ttk.PanedWindow(root), status)          # not shown: no error


def test_window_size_follows_the_screen() -> None:
    """Nine tenths of the screen, between 1280 x 900 and 1600 x 1200, never more than it."""
    class Screen:                         # pylint: disable=too-few-public-methods
        """Stand-in for the root window with a given screen size."""

        def __init__(self, width: int, height: int) -> None:
            """Remember the size.

            Args:
                width: Screen width.
                height: Screen height.
            """
            self.winfo_screenwidth = lambda: width
            self.winfo_screenheight = lambda: height

    size = MediaToolsApp.window_size
    assert size(Screen(1920, 1080)) == "1600x972"          # type: ignore[arg-type]
    assert size(Screen(3840, 2160)) == "1600x1200"         # type: ignore[arg-type]
    assert size(Screen(1366, 768)) == "1280x768"           # type: ignore[arg-type]
