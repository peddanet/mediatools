"""Dialog for shrink jobs: every job so far, and the form to add or change a queued job."""
from __future__ import annotations

import argparse
import shlex
import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox, ttk

from mediatools.gui.commandform import CommandForm
from mediatools.gui.commandspec import CommandSpec
from mediatools.gui.consoleview import ConsoleView
from mediatools.gui.joblist import JobList
from mediatools.gui.shrinkmonitor import ShrinkMonitor
from mediatools.shrink.jobregistry import JobEntry
from mediatools.shrink.queuecommand import QueueCommand
from mediatools.shrink.shrinkqueue import ShrinkQueue


class JobDialog:
    """Window with every shrink job so far and the shrink form.

    Opened by *Add* (a new job) or *Change* (a waiting job) of the shrink
    tab. The upper part lists every remembered job (:class:`JobList`):
    selecting one fills the form with its folder and options, *Add to
    queue* there puts it into the queue as it is, *Remove* and *Remove
    finished* clean the list. The lower part is the shrink form with a
    folder chooser and all options. Its buttons start the job now (*Run*)
    or put it into the queue (*Add to queue*); when a waiting job is being
    changed, *Save changes* replaces it in its place. Both parts are
    separated by a sash. Closing the window only hides it.

    Attributes:
        window: The dialog window.
        form: The shrink form.
        joblist: Every remembered job.
        queue: The shrink queue.
        checker: Checks arguments like ``mediatools queue --add``.
        on_change: Called after the queue changed (refresh of the shrink tab).
        position: Position of the waiting job being changed, or None.
        mode: Text about what the dialog does now.
        status: Result of the last action.
    """

    def __init__(self, master: tk.Misc, spec: CommandSpec, console: ConsoleView,
                 parse: Callable[[list[str]], argparse.Namespace], **extra) -> None:
        """Build the (hidden) dialog.

        Args:
            master: Main window.
            spec: The shrink command.
            console: Console of the shrink tab (output of *Run*).
            parse: Parses ``shrink`` arguments.
            **extra: ``store`` (:class:`FormStore`), ``ask`` (yes/no
                question), ``monitor`` (:class:`ShrinkMonitor`), ``queue``
                (:class:`ShrinkQueue`) and ``on_change`` (callable).
        """
        self.queue: ShrinkQueue = extra.get("queue") or ShrinkQueue.default()
        self.checker = QueueCommand(self.queue, parse, lambda _args: 0)
        self.on_change: Callable[[], object] = extra.get("on_change") or (lambda: None)
        self.position: int | None = None
        self.window = tk.Toplevel(master)
        self.window.title("Shrink jobs")
        self.window.geometry("1200x820")
        self.window.withdraw()
        self.window.protocol("WM_DELETE_WINDOW", self.window.withdraw)
        self.mode = tk.StringVar(master=master)
        self.status = tk.StringVar(master=master)
        header = ttk.Frame(self.window, padding=(8, 8, 8, 0))
        header.pack(fill="x")
        ttk.Label(header, textvariable=self.mode, style="Title.TLabel").pack(side="left")
        ttk.Button(header, text="Close", command=self.window.withdraw).pack(side="right")
        self.save = ttk.Button(header, text="Save changes", style="Accent.TButton",
                               command=self.save_changes)
        ttk.Label(header, textvariable=self.status, style="Hint.TLabel").pack(side="left", padx=12)
        panes = ttk.PanedWindow(self.window, orient="vertical")
        panes.pack(fill="both", expand=True, padx=8, pady=8)
        monitor = extra.get("monitor") or ShrinkMonitor()
        self.joblist = JobList(panes, monitor, self.picked, confirm=extra.get("ask"), pack=False,
                               start_text="Add to queue")
        panes.add(self.joblist.frame, weight=1)
        self.form = CommandForm(panes, spec, console, extra.get("store"),
                                extra.get("ask") or messagebox.askyesno)
        self.form.enqueuer = self.enqueue
        panes.add(self.form.frame, weight=2)

    def open(self, position: int | None = None, args: list[str] | None = None) -> None:
        """Show the dialog for a new job or for a waiting job.

        Args:
            position: Position of the waiting job to change, or None.
            args: Its arguments (fill the form), or None to keep the form.
        """
        self.position = position
        if position is None:
            self.mode.set("New job: choose a job below or fill the form")
            self.save.pack_forget()
        else:
            self.mode.set(f"Change waiting job {position}")
            self.save.pack(side="right", padx=6)
        if args:
            self.fill(args)
        self.status.set("")
        self.joblist.refresh()
        self.window.deiconify()
        self.window.lift()

    def fill(self, args: list[str]) -> None:
        """Fill the form from ``shrink`` arguments (folder first).

        Args:
            args: Arguments; unknown words are ignored.
        """
        self.form.reset()
        words = list(args)
        if words and not words[0].startswith("-"):
            self.form.set_value("path", words.pop(0))
        while words:
            option = self.form.spec.option(words.pop(0))
            if option is None or option.dest not in self.form.variables:
                continue
            if option.kind == "flag":
                self.form.set_value(option.dest, True)
            elif words:
                self.form.set_value(option.dest, words.pop(0))

    def form_args(self) -> list[str] | None:
        """Return the checked ``shrink`` arguments of the form.

        Returns:
            Arguments with an absolute folder, or None (reason in
            :attr:`status`).
        """
        try:
            return self.checker.checked(shlex.join(self.form.spec.argv(self.form.values())[1:]))
        except ValueError as err:
            self.status.set(f"not taken: {err}")
            return None

    def enqueue(self, first: bool = False) -> bool:
        """Put the job of the form into the queue.

        Args:
            first: Put it first instead of last.

        Returns:
            True if it was added.
        """
        args = self.form_args()
        if args is None:
            return False
        position = self.queue.add(args, front=first)
        self.status.set(f"added to the queue as job {position}")
        self.on_change()
        return True

    def save_changes(self) -> bool:
        """Replace the waiting job being changed by the job of the form.

        Returns:
            True if it was replaced.
        """
        if self.position is None:
            return False
        args = self.form_args()
        if args is None:
            return False
        try:
            self.queue.replace(self.position, args)
        except ValueError as err:
            self.status.set(f"not saved: {err}")
            return False
        self.status.set(f"waiting job {self.position} changed")
        self.on_change()
        return True

    def picked(self, entry: JobEntry, start: bool) -> None:
        """Fill the form with a remembered job; with *Add to queue* also queue it.

        Args:
            entry: The job.
            start: True for *Add to queue* or a double click.
        """
        for dest, value in JobList.form_values(entry).items():
            if dest in self.form.variables:
                self.form.set_value(dest, value)
        if start:
            self.enqueue()
