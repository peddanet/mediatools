"""Main window of the mediatools GUI."""
from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

from mediatools.core.config import Config
from mediatools.core.datadir import DataDir
from mediatools.core.versioninfo import VersionInfo
from mediatools.gui.appearance import Appearance
from mediatools.gui.catalog import CommandCatalog
from mediatools.gui.commandform import CommandForm
from mediatools.gui.configpanel import ConfigPanel
from mediatools.gui.consoleview import ConsoleView
from mediatools.gui.desktopentry import DesktopEntry
from mediatools.gui.filespanel import FilesPanel
from mediatools.gui.formstore import FormStore
from mediatools.gui.savingspanel import SavingsPanel
from mediatools.gui.jobpicker import JobPicker
from mediatools.gui.shrinkmonitor import ShrinkMonitor
from mediatools.gui.shrinkpanel import ShrinkPanel
from mediatools.gui.updatebar import UpdateBar
from mediatools.shrink.jobregistry import JobEntry
from mediatools.shrink.jobspec import JobSpec


class MediaToolsApp:
    """One window with a tab per task; every command of the command line.

    Tabs: *shrink* (dashboard of the running shrink and the form to start
    one), one tab per further subcommand, *savings* (freed space per
    session or day), *config* (editor of ``mediatools.ini``) and *files*
    (logs, reports, plans). ``status``,
    ``stop`` and ``reload`` are buttons of the shrink dashboard; ``attach``
    is replaced by the dashboard. Every command runs as a child process
    with the same interpreter as the GUI; one command per tab may run at a
    time, different tabs may run at the same time.

    Since 1.6.0: the shrink tab lists the shrink jobs so far (selecting one
    fills the form, *Continue* runs it), and since 1.6.2 its *Run* first
    offers them in a dialog (:class:`JobPicker`); the menu *View* changes the font
    size (Ctrl++ / Ctrl+- / Ctrl+0) and the colour scheme - the desktop's
    (default, followed live), light or dark (:class:`Appearance`); a newly
    installed release is noticed (checked every :attr:`UPDATE_SECONDS`) and
    a restart offered in a bar above the tabs.

    Attributes:
        root: Tk main window.
        catalog: Subcommands read from the argument parser.
        store: Remembered form values.
        forms: Command forms by subcommand.
        tabs: Tab of each command form by subcommand.
        consoles: Output panes of all tabs.
        notebook: The tabs.
        icon: Window icon (kept so Tk does not discard it).
        ask: Confirmation dialog, replaceable in tests.
        appearance: Font size and colour scheme; holds the configuration
            file (``[gui]`` settings).
        updates: Bar shown when a newer release is installed.
        restarter: Replaces the process by a new GUI (replaceable in tests).
    """

    PANEL_COMMANDS = ("status", "stop", "reload", "config", "savings")
    """Subcommands covered by panels instead of a form."""

    UPDATE_SECONDS = 60

    def __init__(self, root: tk.Tk, catalog: CommandCatalog, store: FormStore | None = None,
                 ask: Callable[[str, str], bool] = messagebox.askyesno, refresh_ms: int = 2000,
                 config: Config | None = None):
        """Build the window.

        Args:
            root: Tk main window.
            catalog: Subcommands of the command line.
            store: Remembered form values; the default file if None.
            ask: Confirmation dialog (title, text) -> yes.
            refresh_ms: Refresh interval of the shrink dashboard (0: manual).
            config: Configuration file; the default file if None.
        """
        self.root = root
        self.catalog = catalog
        self.store = store or FormStore()
        self.ask = ask
        self.forms: dict[str, CommandForm] = {}
        self.consoles: list[ConsoleView] = []
        self.tabs: dict[str, ttk.Frame] = {}
        self.appearance = Appearance(root, config or Config())
        self.restarter: Callable[[], None] = self._exec_gui
        root.title(f"mediatools {VersionInfo().describe()}")
        self.icon = tk.PhotoImage(master=root, file=os.path.join(DesktopEntry.ICON_DIR,
                                                                 "mediatools-64.png"))
        root.iconphoto(True, self.icon)
        root.geometry("1280x900")
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind_all("<Control-q>", lambda _event: self.close())
        for sequence, steps in (("<Control-plus>", 1), ("<Control-equal>", 1),
                                ("<Control-KP_Add>", 1), ("<Control-minus>", -1),
                                ("<Control-KP_Subtract>", -1), ("<Control-0>", 0)):
            root.bind_all(sequence, self._zoom_key(steps))
        self._menu()
        self.updates = UpdateBar(root, self.restart)
        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill="both", expand=True, padx=6, pady=6)
        for spec in catalog.commands():
            if spec.name in self.PANEL_COMMANDS:
                continue
            frame = ttk.Frame(self.notebook, padding=6)
            panes = ttk.PanedWindow(frame, orient="vertical")
            panes.pack(fill="both", expand=True)
            if spec.name == "shrink":
                panes.add(ShrinkPanel(panes, refresh_ms=refresh_ms,
                                      on_pick=self.pick_job).frame, weight=2)
            console = ConsoleView(panes, height=6 if spec.name == "shrink" else 12)
            form = CommandForm(panes, spec, console, self.store, ask)
            if spec.name == "shrink":
                form.chooser = self.choose_shrink_run
            panes.add(form.frame, weight=1)
            panes.add(console.frame, weight=2)
            self.forms[spec.name] = form
            self.tabs[spec.name] = frame
            self.consoles.append(console)
            self.notebook.add(frame, text=spec.name)
        self.notebook.add(SavingsPanel(self.notebook).frame, text="savings")
        self.notebook.add(ConfigPanel(self.notebook).frame, text="config")
        self.notebook.add(FilesPanel(self.notebook, on_undo=self.undo_plan).frame, text="files")
        self.appearance.apply()
        if refresh_ms:
            root.after(self.UPDATE_SECONDS * 1000, self.check_update)
            self.appearance.watch()

    def _menu(self) -> None:
        """Build the menu bar."""
        menubar = tk.Menu(self.root)
        program = tk.Menu(menubar, tearoff=False)
        program.add_command(label="Add to application menu", command=self.install_desktop)
        program.add_command(label="Remove from application menu", command=self.remove_desktop)
        program.add_separator()
        program.add_command(label="Quit", accelerator="Ctrl+Q", command=self.close)
        menubar.add_cascade(label="mediatools", menu=program)
        view = tk.Menu(menubar, tearoff=False)
        view.add_command(label="Larger text", accelerator="Ctrl++", command=lambda: self.zoom(1))
        view.add_command(label="Smaller text", accelerator="Ctrl+-",
                         command=lambda: self.zoom(-1))
        view.add_command(label="Normal size", accelerator="Ctrl+0", command=lambda: self.zoom(0))
        view.add_separator()
        choice = self.appearance.choice
        for label, value in (("Scheme of the desktop", "auto"), ("Light scheme", "light"),
                             ("Dark scheme", "dark")):
            view.add_radiobutton(label=label, value=value, variable=choice,
                                 command=lambda: self.appearance.set_theme(choice.get()))
        menubar.add_cascade(label="View", menu=view)
        helpmenu = tk.Menu(menubar, tearoff=False)
        helpmenu.add_command(label="About", command=self.about)
        menubar.add_cascade(label="Help", menu=helpmenu)
        self.root.configure(menu=menubar)

    @staticmethod
    def program_path() -> str:
        """Return the program the application menu entry starts.

        Returns:
            ``~/scripts/mediatools`` if it exists, else ``mediatools`` from
            the search path, else this interpreter's script.
        """
        return DesktopEntry.default_program()

    def about_text(self) -> str:
        """Return the text of the About dialog.

        Returns:
            Version, folders and interpreter.
        """
        return (f"mediatools {VersionInfo().describe()}\n\n"
                f"configuration: {DataDir().config_dir}\n"
                f"logs and state: {DataDir().state_dir}\n"
                f"kept data: {DataDir().data_dir}\n"
                f"Python: {sys.version.split()[0]} ({sys.executable})\n"
                f"Tk: {tk.TkVersion}")

    def about(self) -> None:
        """Show version and locations."""
        messagebox.showinfo("About mediatools", self.about_text(), parent=self.root)

    def install_desktop(self) -> None:
        """Write the application menu entry."""
        path = DesktopEntry(self.program_path()).install()
        self.appearance.save("desktop_entry", "yes")
        messagebox.showinfo("Application menu", "written: " + path, parent=self.root)

    def remove_desktop(self) -> None:
        """Remove the application menu entry."""
        removed = DesktopEntry(self.program_path()).remove()
        self.appearance.save("desktop_entry", "no")
        messagebox.showinfo("Application menu", "removed" if removed else "there was no entry",
                            parent=self.root)

    def _zoom_key(self, steps: int) -> Callable[[tk.Event], None]:
        """Return the key handler of one zoom shortcut.

        Args:
            steps: See :meth:`zoom`.

        Returns:
            Handler that ignores its event.
        """
        def handler(_event: tk.Event) -> None:
            self.zoom(steps)
        return handler

    def zoom(self, steps: int) -> None:
        """Change the text size (see :meth:`Appearance.zoom`).

        Args:
            steps: +1 larger, -1 smaller, 0 normal.
        """
        self.appearance.zoom(steps)

    @staticmethod
    def job_values(entry: JobEntry) -> dict[str, str]:
        """Return the shrink form values of a remembered job.

        Args:
            entry: The job.

        Returns:
            Values by ``dest`` of the shrink form.
        """
        spec = entry.spec
        newer = spec.as_dict()["newer_than"]
        jobs = entry.settings.get("jobs")
        return {"path": spec.root, "crf": str(entry.settings.get("crf") or 28),
                "preset": str(entry.settings.get("preset") or ""),
                "order": str(entry.settings.get("order") or "largest"),
                "jobs": "" if jobs in (None, 1) else str(jobs),
                "threads": str(entry.settings.get("threads") or ""),
                "ext": ",".join(ext.lstrip(".") for ext in sorted(spec.extensions)),
                "min_seconds": f"{spec.min_seconds:g}",
                "min_size": JobSpec.size_text(spec.min_size) if spec.min_size else "",
                "newer_than": str(newer or "")}

    def pick_job(self, entry: JobEntry, start: bool = False) -> bool:
        """Fill the shrink form with a job; optionally run it.

        Args:
            entry: The chosen job.
            start: Run the form afterwards.

        Returns:
            True if the form was filled (and, with ``start``, started).
        """
        form = self.forms.get("shrink")
        if form is None:
            return False
        for dest, value in self.job_values(entry).items():
            if dest in form.variables:
                form.set_value(dest, value)
        return form.start() if start else True

    def choose_shrink_run(self) -> bool:
        """*Run* of the shrink tab: offer the earlier jobs first (:class:`JobPicker`).

        Without any remembered job the form runs at once.

        Returns:
            True if the shrink was started now; False while the dialog is open.
        """
        form = self.forms["shrink"]
        monitor = ShrinkMonitor()
        if not monitor.registry().entries():
            return form.start()
        JobPicker(self.root, monitor, form.line.get(), self.run_choice)
        return False

    def run_choice(self, entry: JobEntry | None) -> bool:
        """Start the shrink chosen in the dialog.

        Args:
            entry: An earlier job (fills the form first), or None for the form.

        Returns:
            True if the shrink was started.
        """
        if entry is not None:
            return self.pick_job(entry, start=True)
        return self.forms["shrink"].start()

    def check_update(self) -> None:
        """Check for a newly installed release; show the bar a little later."""
        self.updates.check()
        self.root.after(5000, lambda: self.updates.show(self.notebook))
        self.root.after(self.UPDATE_SECONDS * 1000, self.check_update)

    def restart(self) -> bool:
        """Close the window (asking about running commands) and start a new GUI.

        A running shrink is not affected; the new window shows it again.

        Returns:
            False if the user kept the window open.
        """
        if not self.close():
            return False
        self.restarter()
        return True

    @staticmethod
    def _exec_gui() -> None:
        """Replace this process by ``mediatools gui`` of the installed release."""
        os.execv(sys.executable, [sys.executable, "-m", "mediatools", "gui"])

    def undo_plan(self, plan: str) -> None:
        """Put a plan file into the flatten form and show that tab.

        Args:
            plan: Plan file in ``plans/``.
        """
        form = self.forms.get("flatten")
        if form is None or form.spec.option("--undo") is None:
            return
        form.set_value("undo", plan)
        self.notebook.select(self.tabs["flatten"])

    def running(self) -> list[ConsoleView]:
        """Return the panes whose command still runs.

        Returns:
            Output panes with a running command.
        """
        return [console for console in self.consoles if console.runner.running]

    def close(self) -> bool:
        """Close the window; running commands are interrupted after a question.

        A shrink in the background is not affected: it is not a child of
        the GUI once it has moved into the background.

        Returns:
            True if the window was closed.
        """
        busy = self.running()
        if busy and not self.ask("Quit mediatools",
                                 f"{len(busy)} command(s) still running in this window. "
                                 "They will be interrupted. Quit anyway?"):
            return False
        for console in busy:
            console.runner.cancel()
        for console in busy:
            try:
                console.runner.wait(5)
            except subprocess.TimeoutExpired:
                console.runner.cancel()          # second request: terminate
        self.root.destroy()
        return True
