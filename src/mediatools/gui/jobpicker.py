"""Dialog shown by *Run* in the shrink tab: continue an earlier job or run the form."""
from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

from mediatools.gui.joblist import JobList
from mediatools.gui.shrinkmonitor import ShrinkMonitor
from mediatools.shrink.jobregistry import JobEntry


class JobPicker:
    """Offers the earlier shrink jobs before a shrink is started from the GUI.

    The GUI counterpart of a plain ``mediatools shrink`` on a terminal
    (:class:`JobChooser`): *Run* in the shrink tab opens this dialog with
    the jobs so far - from the command line or the GUI, the most recently
    active first, with progress and settings - and one button for the
    values currently in the form. *Continue* (or a double click) takes the
    selected job, *Run as in the form* the form; either way ``on_choice``
    is called once and the dialog closes. *Cancel* or closing the window
    starts nothing.

    Attributes:
        window: The dialog window.
        jobs: The job list inside it.
        on_choice: Receives the chosen job, or None for the form values.
    """

    def __init__(self, master: tk.Misc, monitor: ShrinkMonitor, form_line: str,
                 on_choice: Callable[[JobEntry | None], object]):
        """Open the dialog.

        Args:
            master: Main window.
            monitor: Source of the job registry.
            form_line: Command line of the current form values (shown).
            on_choice: Called with the chosen job, or None for the form.
        """
        self.on_choice = on_choice
        self.window = tk.Toplevel(master)
        self.window.title("Run shrink")
        self.window.transient(master)                      # type: ignore[call-overload]
        frame = ttk.Frame(self.window, padding=10)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Continue an earlier shrink job, or run the values of the form.",
                  style="Title.TLabel").pack(anchor="w", pady=(0, 6))
        self.jobs = JobList(frame, monitor, self._picked)
        self.jobs.jobs.configure(height=8)
        self.jobs.refresh()
        footer = ttk.Frame(frame)
        footer.pack(fill="x", pady=(6, 0))
        ttk.Button(footer, text="Run as in the form", command=lambda: self.choose(None)).pack(
            side="left")
        ttk.Label(footer, text=form_line, style="Hint.TLabel").pack(side="left", padx=8)
        ttk.Button(footer, text="Cancel", command=self.window.destroy).pack(side="right")
        self.window.protocol("WM_DELETE_WINDOW", self.window.destroy)

    def _picked(self, entry: JobEntry, start: bool) -> None:
        """React to the job list: start only on *Continue* or a double click.

        Args:
            entry: Selected job.
            start: True for *Continue* or a double click.
        """
        if start:
            self.choose(entry)

    def choose(self, entry: JobEntry | None) -> None:
        """Close the dialog and hand over the choice.

        Args:
            entry: The chosen job, or None for the form values.
        """
        self.window.destroy()
        self.on_choice(entry)
