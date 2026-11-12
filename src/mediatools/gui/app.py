"""Main window of the mediatools GUI."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

from mediatools.core.datadir import DataDir
from mediatools.core.versioninfo import VersionInfo
from mediatools.gui.catalog import CommandCatalog
from mediatools.gui.commandform import CommandForm
from mediatools.gui.configpanel import ConfigPanel
from mediatools.gui.consoleview import ConsoleView
from mediatools.gui.desktopentry import DesktopEntry
from mediatools.gui.filespanel import FilesPanel
from mediatools.gui.formstore import FormStore
from mediatools.gui.savingspanel import SavingsPanel
from mediatools.gui.shrinkpanel import ShrinkPanel


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

    Attributes:
        root: Tk main window.
        catalog: Subcommands read from the argument parser.
        store: Remembered form values.
        forms: Command forms by subcommand.
        tabs: Tab of each command form by subcommand.
        consoles: Output panes of all tabs.
        notebook: The tabs.
        ask: Confirmation dialog, replaceable in tests.
    """

    PANEL_COMMANDS = ("status", "stop", "reload", "config", "savings")
    """Subcommands covered by panels instead of a form."""

    def __init__(self, root: tk.Tk, catalog: CommandCatalog, store: FormStore | None = None,
                 ask: Callable[[str, str], bool] = messagebox.askyesno, refresh_ms: int = 2000):
        """Build the window.

        Args:
            root: Tk main window.
            catalog: Subcommands of the command line.
            store: Remembered form values; the default file if None.
            ask: Confirmation dialog (title, text) -> yes.
            refresh_ms: Refresh interval of the shrink dashboard (0: manual).
        """
        self.root = root
        self.catalog = catalog
        self.store = store or FormStore()
        self.ask = ask
        self.forms: dict[str, CommandForm] = {}
        self.consoles: list[ConsoleView] = []
        self.tabs: dict[str, ttk.Frame] = {}
        root.title(f"mediatools {VersionInfo().describe()}")
        root.geometry("1280x900")
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind_all("<Control-q>", lambda _event: self.close())
        self._menu()
        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill="both", expand=True, padx=6, pady=6)
        for spec in catalog.commands():
            if spec.name in self.PANEL_COMMANDS:
                continue
            frame = ttk.Frame(self.notebook, padding=6)
            panes = ttk.PanedWindow(frame, orient="vertical")
            panes.pack(fill="both", expand=True)
            if spec.name == "shrink":
                panes.add(ShrinkPanel(panes, refresh_ms=refresh_ms).frame, weight=2)
            console = ConsoleView(panes, height=6 if spec.name == "shrink" else 12)
            form = CommandForm(panes, spec, console, self.store, ask)
            panes.add(form.frame, weight=1)
            panes.add(console.frame, weight=2)
            self.forms[spec.name] = form
            self.tabs[spec.name] = frame
            self.consoles.append(console)
            self.notebook.add(frame, text=spec.name)
        self.notebook.add(SavingsPanel(self.notebook).frame, text="savings")
        self.notebook.add(ConfigPanel(self.notebook).frame, text="config")
        self.notebook.add(FilesPanel(self.notebook, on_undo=self.undo_plan).frame, text="files")

    def _menu(self) -> None:
        """Build the menu bar."""
        menubar = tk.Menu(self.root)
        program = tk.Menu(menubar, tearoff=False)
        program.add_command(label="Add to application menu", command=self.install_desktop)
        program.add_command(label="Remove from application menu", command=self.remove_desktop)
        program.add_separator()
        program.add_command(label="Quit", accelerator="Ctrl+Q", command=self.close)
        menubar.add_cascade(label="mediatools", menu=program)
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
        link = os.path.expanduser("~/scripts/mediatools")
        if os.path.exists(link):
            return link
        return shutil.which("mediatools") or os.path.abspath(sys.argv[0])

    def about_text(self) -> str:
        """Return the text of the About dialog.

        Returns:
            Version, data folder and interpreter.
        """
        return (f"mediatools {VersionInfo().describe()}\n\n"
                f"data folder: {DataDir().root}\n"
                f"Python: {sys.version.split()[0]} ({sys.executable})\n"
                f"Tk: {tk.TkVersion}")

    def about(self) -> None:
        """Show version and locations."""
        messagebox.showinfo("About mediatools", self.about_text(), parent=self.root)

    def install_desktop(self) -> None:
        """Write the application menu entry."""
        path = DesktopEntry(self.program_path()).install()
        messagebox.showinfo("Application menu", "written: " + path, parent=self.root)

    def remove_desktop(self) -> None:
        """Remove the application menu entry."""
        removed = DesktopEntry(self.program_path()).remove()
        messagebox.showinfo("Application menu", "removed" if removed else "there was no entry",
                            parent=self.root)

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
