"""List of the shrink jobs so far, with progress; selecting one fills the form."""
from __future__ import annotations

import os
import tkinter as tk
from collections.abc import Callable
from tkinter import font as tkfont
from tkinter import ttk

from mediatools.gui.shrinkmonitor import ShrinkMonitor
from mediatools.shrink.jobregistry import JobEntry


class JobList:
    """Shows the remembered shrink jobs (:class:`JobRegistry`).

    One row per job, the most recently active first: last activity, a
    progress bar with percent (done against still to go), the figures in
    GiB, the settings of the last run and the folder with its filters. The
    list is filled again only when ``jobs.json`` or the ledger changed.
    Selecting a row hands the job to ``on_pick`` (fill the shrink form);
    *Continue* or a double click also asks to start it.

    Attributes:
        jobs: The list widget.
        monitor: Source of the registry.
        entries: Jobs shown, by row id.
        on_pick: Called with a job and whether to start it, or None.
        signature: State of the job files when the list was last filled.
    """

    def __init__(self, master: tk.Misc, monitor: ShrinkMonitor,
                 on_pick: Callable[[JobEntry, bool], object] | None = None):
        """Build the list.

        Args:
            master: Parent widget.
            monitor: Source of the registry.
            on_pick: Receives a selected job and True to start it.
        """
        self.monitor = monitor
        self.on_pick = on_pick
        self.entries: dict[str, JobEntry] = {}
        self.signature: tuple = ()
        box = ttk.Labelframe(master, text="Shrink jobs (select: fill the form)", padding=4)
        box.pack(fill="x", pady=(4, 6))
        columns = ("last", "progress", "done", "settings", "job")
        self.jobs = ttk.Treeview(box, columns=columns, show="headings", height=3,
                                 selectmode="browse")
        digit = tkfont.nametofont("TkDefaultFont", root=master).measure("0")
        for name, title, chars, stretch in (("last", "last activity", 18, False),
                                            ("progress", "progress", 28, False),
                                            ("done", "done / to go", 22, False),
                                            ("settings", "settings", 16, False),
                                            ("job", "folder and filters", 40, True)):
            self.jobs.heading(name, text=title, anchor="w")
            self.jobs.column(name, width=chars * digit, stretch=stretch, anchor="w")
        self.jobs.pack(side="left", fill="both", expand=True)
        side = ttk.Frame(box)
        side.pack(side="left", fill="y", padx=(6, 0))
        ttk.Button(side, text="Continue", style="Accent.TButton",
                   command=lambda: self.pick(True)).pack(fill="x")
        self.jobs.bind("<<TreeviewSelect>>", lambda _event: self.pick(False))
        self.jobs.bind("<Double-1>", lambda _event: self.pick(True))

    def refresh(self) -> None:
        """Fill the list again if the job files changed."""
        registry = self.monitor.registry()
        signature = tuple(self._stamp(path) for path in self.monitor.job_files())
        if signature == self.signature:
            return
        self.signature = signature
        selected = self.jobs.selection()
        chosen = self.entries[selected[0]].spec.key if selected else None
        self.jobs.delete(*self.jobs.get_children())
        self.entries = {}
        gib = 1024 ** 3
        for entry in registry.entries():
            settings = f"crf {entry.settings.get('crf', 28)}"
            if entry.settings.get("preset") not in (None, "medium"):
                settings += f", {entry.settings['preset']}"
            if entry.settings.get("jobs") not in (None, 1):
                settings += f", {entry.settings['jobs']} jobs"
            item = self.jobs.insert("", "end", values=(
                entry.last, f"{entry.gauge(16)} {entry.fraction * 100:5.1f} %",
                f"{entry.read / gib:.1f} / {entry.todo_bytes / gib:.1f} GiB", settings,
                entry.spec.label()))
            self.entries[item] = entry
            if entry.spec.key == chosen:
                self.jobs.selection_set(item)

    @staticmethod
    def _stamp(path: str) -> tuple[float, int]:
        """Return modification time and size of a file.

        Args:
            path: File.

        Returns:
            ``(mtime, size)``; zeros if missing.
        """
        try:
            info = os.stat(path)
        except OSError:
            return 0.0, 0
        return info.st_mtime, info.st_size

    def pick(self, start: bool) -> object:
        """Hand the selected job to ``on_pick``.

        Args:
            start: Also start it.

        Returns:
            The result of ``on_pick``, or None without a selection.
        """
        selected = self.jobs.selection()
        if not selected or self.on_pick is None:
            return None
        return self.on_pick(self.entries[selected[0]], start)
