"""1.3.0 GUI: the Tk widgets (run with a display; tools/check.sh uses Xvfb)."""
import os
import sys
import time
import tkinter as tk
from pathlib import Path
from typing import Callable, Iterator

import pytest

from test_gui_logic import write_run
from mediatools.cli import Cli
from mediatools.core.config import Config
from mediatools.gui.app import MediaToolsApp
from mediatools.gui.catalog import CommandCatalog
from mediatools.gui.commandform import CommandForm
from mediatools.gui.commandspec import CommandSpec
from mediatools.gui.configpanel import ConfigPanel
from mediatools.gui.consoleview import ConsoleView
from mediatools.gui.filespanel import FilesPanel
from mediatools.gui.formstore import FormStore
from mediatools.gui.jobrunner import JobRunner
from mediatools.gui.launcher import GuiLauncher
from mediatools.gui.opener import Opener
from mediatools.gui.optionspec import OptionSpec
from mediatools.gui.shrinkmonitor import ShrinkMonitor
from mediatools.gui.shrinkpanel import ShrinkPanel


class FakeOpener(Opener):
    """Records instead of opening."""

    def __init__(self) -> None:
        """Start with no calls."""
        super().__init__("/bin/true")
        self.opened: list[str] = []

    def open(self, path: str) -> str:
        """Record a path.

        Args:
            path: File or folder.

        Returns:
            Message.
        """
        self.opened.append(path)
        return "opened " + path


@pytest.fixture(name="root")
def fixture_root() -> Iterator[tk.Tk]:
    """Give a hidden Tk main window.

    Yields:
        The window; destroyed afterwards.
    """
    assert os.environ.get("DISPLAY"), "the Tk tests need a display (tools/check.sh uses Xvfb)"
    window = tk.Tk()
    window.withdraw()
    yield window
    try:
        window.destroy()
    except tk.TclError:
        pass                                     # already destroyed by the test


def pump(root: tk.Tk, until: Callable[[], object], seconds: float = 20.0) -> None:
    """Process Tk events until a condition holds.

    Args:
        root: Main window.
        until: Callable without arguments returning a truth value.
        seconds: Time limit.
    """
    end = time.monotonic() + seconds
    while not until() and time.monotonic() < end:
        root.update()
        time.sleep(0.01)
    assert until()


def python_runner(code: str) -> JobRunner:
    """Return a runner of a Python snippet.

    Args:
        code: Python source.

    Returns:
        The runner.
    """
    return JobRunner([sys.executable, "-c", code])


def test_console_runs_answers_and_cancels(root: tk.Tk) -> None:
    """Output and questions appear, answers go in, the end is reported.

    Args:
        root: Main window (fixture).
    """
    codes: list[int] = []
    console = ConsoleView(root, python_runner("a = input('Name? '); print('hi ' + a)"),
                          on_finish=codes.append, poll_ms=10)
    console.write("")
    assert console.run([], "demo")
    assert not console.run([], "again")
    assert "still running" in console.state.get()
    pump(root, lambda: "Name? " in console.text.get("1.0", "end"))
    console.answer.insert(0, "Bob")
    console.send()
    pump(root, lambda: codes == [0])
    assert "hi Bob" in console.text.get("1.0", "end") and "[exit code 0]" in \
        console.text.get("1.0", "end")
    console.send()
    assert console.state.get() == "no command is running"
    console.cancel()
    assert console.state.get() == "no command is running"
    console.write("a\r\nb\rc")
    assert "a\nb\nc" in console.text.get("1.0", "end")
    console.clear()
    assert console.text.get("1.0", "end").strip() == ""
    plain = ConsoleView(root, python_runner("print(1)"), poll_ms=10)
    plain.run([], "x")
    pump(root, lambda: "finished" in plain.state.get())


def spec() -> CommandSpec:
    """Return a command with every kind of field.

    Returns:
        The command.
    """
    return CommandSpec("demo", "demo help", options=[
        OptionSpec("path", path=True), OptionSpec("mode", kind="choice", choices=["a", "b"],
                                                  required=True),
        OptionSpec("level", ["--level"], kind="choice", choices=["x", "y"]),
        OptionSpec("crf", ["--crf"], kind="int", default=28, help="quality"),
        OptionSpec("set", ["--set"], kind="list"),
        OptionSpec("undo", ["--undo"], path=True),
        OptionSpec("dry_run", ["--dry-run"], kind="flag")],
        program=["mediatools"])


def test_form_fields_line_and_values(root: tk.Tk, tmp_path: Path,
                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """Fields hold their values; the command line follows every change.

    Args:
        root: Main window (fixture).
        tmp_path: Temporary directory (fixture).
        monkeypatch: Patcher (fixture).
    """
    store = FormStore(str(tmp_path / "gui.json"))
    store.save("demo", {"mode": "b", "set": ["a.b=1"]})
    form = CommandForm(root, spec(), ConsoleView(root), store)
    assert form.line.get() == "mediatools demo b --crf 28 --set a.b=1"
    form.set_value("path", "/data")
    form.set_value("set", "c.d=2")
    form.set_value("dry_run", True)
    assert form.values()["set"] == ["c.d=2"]
    assert form.line.get() == "mediatools demo /data b --crf 28 --set c.d=2 --dry-run"
    form.set_value("crf", "x")
    assert form.line.get() == "incomplete: --crf: not a number"
    form.reset()
    assert form.line.get() == "incomplete: mode is required"
    assert form.preview_option() == "--dry-run"
    form.copy()
    assert root.clipboard_get() == form.line.get()
    chosen = {"askdirectory": "/picked", "askopenfilename": "/plan.json"}
    monkeypatch.setattr("mediatools.gui.commandform.filedialog.askdirectory",
                        lambda **_kw: chosen["askdirectory"])
    monkeypatch.setattr("mediatools.gui.commandform.filedialog.askopenfilename",
                        lambda **_kw: chosen["askopenfilename"])
    form.browse(spec().options[0])
    form.browse(spec().options[5])
    form.browse(spec().options[4])                # list field: nothing to browse
    assert form.values()["path"] == "/picked" and form.values()["undo"] == "/plan.json"
    chosen["askdirectory"] = ""
    form.browse(spec().options[0])
    assert form.values()["path"] == "/picked"


def test_form_runs_after_confirmation(root: tk.Tk, tmp_path: Path) -> None:
    """Changing runs need a yes; previews and read-only runs do not.

    Args:
        root: Main window (fixture).
        tmp_path: Temporary directory (fixture).
    """
    answers: list[bool] = [False, True]
    asked: list[str] = []

    def ask(title: str, text: str) -> bool:
        """Answer from the list.

        Args:
            title: Dialog title.
            text: Dialog text.

        Returns:
            Next answer.
        """
        asked.append(title + text)
        return answers.pop(0)

    console = ConsoleView(root, python_runner("import sys; print(sys.argv[1:])"), poll_ms=10)
    store = FormStore(str(tmp_path / "gui.json"))
    form = CommandForm(root, spec(), console, store, ask)
    form.set_value("mode", "a")
    assert not form.run() and console.state.get() == "not started"
    assert form.run() and "may change files" in asked[-1]
    pump(root, lambda: "finished" in console.state.get())
    assert store.load("demo")["mode"] == "a"
    assert form.preview() and len(asked) == 2
    pump(root, lambda: "--dry-run" in console.text.get("1.0", "end")
         and console.state.get().startswith("finished"))
    form.set_value("mode", "")
    assert not form.run() and console.state.get() == "not started: mode is required"
    plain = CommandForm(root, CommandSpec("progress"), console, None, ask)
    assert plain.preview_option() is None
    pump(root, lambda: not console.runner.running)
    assert plain.preview() and len(asked) == 2


def test_shrink_panel(root: tk.Tk, datadir: Path) -> None:
    """The dashboard shows a run and passes stop, reload and log requests.

    Args:
        root: Main window (fixture).
        datadir: Data directory (fixture).
    """
    opener = FakeOpener()
    idle = ShrinkPanel(root, ShrinkMonitor(), opener, refresh_ms=0)
    assert "no shrink is encoding" in idle.progress.get()
    idle.open_log()
    assert idle.message.get() == "no log of a running shrink"
    log = write_run(datadir, "total [#...]  40.0 % | 1/2 files\nfile 9 %\n", log_lines=500)
    panel = ShrinkPanel(root, ShrinkMonitor(), opener, refresh_ms=10)
    pump(root, lambda: panel.meter["value"] == 40.0)
    assert panel.report.get().startswith("shrink: running")
    assert "line 499" in panel.log.get("1.0", "end")
    assert panel.overall.get().startswith("space  session: 0.0 GiB read")
    panel.open_log()
    assert opener.opened == [log]
    panel.stop()
    assert panel.message.get().startswith("stop requested")
    panel.reload()
    assert panel.message.get().startswith("reload requested")


def test_config_panel(root: tk.Tk, datadir: Path) -> None:
    """Template shown, invalid text refused, valid text saved, folder opened.

    Args:
        root: Main window (fixture).
        datadir: Data directory (fixture).
    """
    opener = FakeOpener()
    panel = ConfigPanel(root, opener=opener)
    assert "missing" in panel.path.get()
    assert "[collection]" in panel.editor.get("1.0", "end")
    panel.create()
    assert panel.message.get().startswith("created ") and Config().exists()
    panel.create()
    assert panel.message.get().startswith("already exists")
    panel.editor.delete("1.0", "end")
    panel.editor.insert("1.0", "no section here")
    assert not panel.save() and panel.message.get().startswith("not saved:")
    panel.editor.delete("1.0", "end")
    panel.editor.insert("1.0", "[shrink]\ncrf_note = test")
    assert panel.save()
    assert Path(Config().path).read_text(encoding="utf-8") == "[shrink]\ncrf_note = test\n"
    assert "crf_note = test" in panel.effective.get("1.0", "end")
    panel.open_folder()
    assert opener.opened == [str(datadir)]


def test_files_panel(root: tk.Tk, datadir: Path) -> None:
    """Files are listed newest first, shown, opened and passed for undo.

    Args:
        root: Main window (fixture).
        datadir: Data directory (fixture).
    """
    (datadir / "logs").mkdir(parents=True)
    (datadir / "plans").mkdir()
    old = datadir / "logs" / "old.log"
    old.write_text("old\n", encoding="utf-8")
    os.utime(old, (1, 1))
    big = datadir / "logs" / "big.log"
    big.write_bytes(b"x" * (FilesPanel.SHOW_BYTES + 10) + b"\nEND")
    plan = datadir / "plans" / "plan.json"
    plan.write_text("{}", encoding="utf-8")
    opener, undone = FakeOpener(), []
    panel = FilesPanel(root, opener=opener, on_undo=undone.append)
    logs = panel.tree.get_children(str(datadir / "logs"))
    assert [os.path.basename(path) for path in logs] == ["big.log", "old.log"]
    assert panel.tree.item(str(big))["values"][0] == "512.0 KiB"
    panel.open_selected()
    assert panel.message.get() == "nothing selected"
    panel.use_as_undo()
    assert panel.message.get() == "select a plan file in plans/"
    panel.tree.selection_set(str(big))
    root.update()
    assert panel.viewer.get("1.0", "2.0").startswith("[showing the last 512 KiB")
    assert panel.viewer.get("1.0", "end").rstrip().endswith("END")
    panel.open_selected()
    assert opener.opened == [str(big)]
    panel.tree.selection_set(str(plan))
    root.update()
    panel.use_as_undo()
    assert undone == [str(plan)] and "plan.json" in panel.message.get()
    panel.tree.selection_set(str(datadir / "plans"))
    root.update()
    panel.show_selected()
    assert FilesPanel.size_text(10) == "10 B" and FilesPanel.size_text(3 * 1024 ** 3) == \
        "3.0 GiB" and FilesPanel.size_text(5 * 1024 ** 4) == "5120.0 GiB"


def test_files_panel_unreadable(root: tk.Tk, datadir: Path,
                                monkeypatch: pytest.MonkeyPatch) -> None:
    """A file that cannot be read shows the error.

    Args:
        root: Main window (fixture).
        datadir: Data directory (fixture).
        monkeypatch: Patcher (fixture).
    """
    (datadir / "state").mkdir(parents=True)
    path = datadir / "state" / "x.json"
    path.write_text("{}", encoding="utf-8")
    panel = FilesPanel(root, opener=FakeOpener())
    panel.tree.selection_set(str(path))

    def deny(*_args, **_kwargs):
        """Refuse to open.

        Args:
            *_args: Ignored.
            **_kwargs: Ignored.

        Raises:
            PermissionError: Always.
        """
        raise PermissionError("denied")

    monkeypatch.setattr("builtins.open", deny)
    panel.show_selected()
    monkeypatch.undo()
    assert "denied" in panel.viewer.get("1.0", "end")


def test_app_tabs_undo_about_and_close(root: tk.Tk, tmp_path: Path,
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """All commands get a tab; undo plans, menu entry and closing work.

    Args:
        root: Main window (fixture).
        tmp_path: Temporary directory (fixture).
        monkeypatch: Patcher (fixture).
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    shown: list[str] = []
    monkeypatch.setattr("mediatools.gui.app.messagebox.showinfo",
                        lambda _title, text, **_kw: shown.append(text))
    answers = [False, True]
    app = MediaToolsApp(root, CommandCatalog(Cli().parser), FormStore(str(tmp_path / "g.json")),
                        ask=lambda _t, _x: answers.pop(0), refresh_ms=0)
    tabs = [app.notebook.tab(tab, "text") for tab in app.notebook.tabs()]
    assert tabs == ["shrink", "update", "progress", "triage", "flatten", "sync", "rename-db",
                    "snapshot", "savings", "config", "files"]
    app.undo_plan("/data/plans/p.json")
    assert app.forms["flatten"].values()["undo"] == "/data/plans/p.json"
    assert app.notebook.tab(app.notebook.select(), "text") == "flatten"
    assert "data folder:" in app.about_text()
    app.about()
    app.install_desktop()
    assert shown[-1].startswith("written: ")
    app.remove_desktop()
    app.remove_desktop()
    assert shown[-2:] == ["removed", "there was no entry"]
    assert app.program_path() == os.path.abspath(sys.argv[0]) or "mediatools" in \
        app.program_path()
    (tmp_path / "home" / "scripts").mkdir(parents=True)
    (tmp_path / "home" / "scripts" / "mediatools").write_text("", encoding="utf-8")
    assert app.program_path() == str(tmp_path / "home" / "scripts" / "mediatools")
    del app.forms["flatten"]
    app.undo_plan("/x")                           # no flatten form: nothing happens
    console = app.consoles[0]
    console.runner = python_runner("import time\nwhile True: time.sleep(0.05)")
    console.runner.start([])
    assert app.running() == [console]
    assert not app.close()                        # answered no
    assert app.close()                            # answered yes: interrupted
    assert console.runner.process is not None and console.runner.process.poll() is not None


def test_app_close_terminates_stubborn_command(root: tk.Tk, tmp_path: Path) -> None:
    """A command that ignores the interrupt is terminated on close.

    Args:
        root: Main window (fixture).
        tmp_path: Temporary directory (fixture).
    """
    app = MediaToolsApp(root, CommandCatalog(Cli().parser), FormStore(str(tmp_path / "g.json")),
                        ask=lambda _t, _x: True, refresh_ms=0)
    console = app.consoles[0]
    console.runner = python_runner("import signal, time\n"
                                   "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
                                   "print('ready', flush=True)\n"
                                   "while True: time.sleep(0.05)")
    console.runner.start([])
    end = time.monotonic() + 10
    text = ""
    while "ready" not in text and time.monotonic() < end:
        text += console.runner.poll()[0]
    real = console.runner.wait
    calls: list[float | None] = []

    def short(timeout: float | None = None) -> int | None:
        """Wait briefly on the first call.

        Args:
            timeout: Requested limit.

        Returns:
            Exit code.
        """
        calls.append(timeout)
        return real(0.2 if len(calls) == 1 else timeout)

    console.runner.wait = short                    # type: ignore[method-assign]
    assert app.close()
    assert console.runner.process is not None and console.runner.process.wait(10) == -15
    console.runner.wait(1)


def test_launcher_opens_the_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """With display and Tk the window is built and its loop entered.

    Args:
        monkeypatch: Patcher (fixture).
    """
    entered: list[tk.Tk] = []

    def loop(self: tk.Tk) -> None:
        """Close at once instead of waiting for the user.

        Args:
            self: The window.
        """
        entered.append(self)
        self.destroy()

    monkeypatch.setattr(tk.Tk, "mainloop", loop)
    monkeypatch.setattr("mediatools.gui.app.ShrinkPanel.refresh", lambda self: None)
    assert GuiLauncher(Cli().parser, "/x", dict(os.environ)).run() == 0
    assert len(entered) == 1
