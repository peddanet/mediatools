"""List of the shrink jobs so far, with progress; selecting one fills the form."""
from __future__ import annotations

import os
import tkinter as tk
from collections.abc import Callable
from tkinter import font as tkfont
from tkinter import messagebox
from tkinter import ttk

from mediatools.gui.shrinkmonitor import ShrinkMonitor
from mediatools.shrink.jobregistry import JobEntry, JobRegistry
from mediatools.shrink.jobspec import JobSpec


class JobList:
    """Shows the remembered shrink jobs (:class:`JobRegistry`).

    One row per job, the most recently active first: last activity, a
    progress bar with percent (done against still to go), the figures in
    GiB, the settings of the last run and the folder with its filters. The
    list is filled again only when ``jobs.json`` or the ledger changed.
    Selecting a row hands the job to ``on_pick`` (fill the shrink form);
    *Continue* or a double click also asks to start it. The list scrolls,
    and the arrow keys move the selection as soon as the list has the
    keyboard focus (a click into it, or Tab).

    *Remove* forgets the selected jobs (several with Ctrl or Shift),
    *Remove finished* every job with nothing left to do; both ask first
    and never remove the job that is running. Only the entries of the list
    go - databases, ledger and videos stay; a removed job comes back when
    it is run again.

    Attributes:
        frame: The box with list and buttons (packed by the caller if
            ``pack`` is False).
        jobs: The list widget.
        monitor: Source of the registry.
        entries: Jobs shown, by row id.
        on_pick: Called with a job and whether to start it, or None.
        confirm: Asks a yes/no question (title, text) before removing.
        message: Result of the last removal (shown next to the buttons).
        signature: State of the job files when the list was last filled.
    """

    def __init__(self, master: tk.Misc, monitor: ShrinkMonitor,
                 on_pick: Callable[[JobEntry, bool], object] | None = None,
                 confirm: Callable[[str, str], bool] | None = None, pack: bool = True,
                 start_text: str = "Continue"):
        """Build the list.

        Args:
            master: Parent widget.
            monitor: Source of the registry.
            on_pick: Receives a selected job and True to start it.
            confirm: Yes/no question before removing; a message box if None.
            pack: Pack the box into ``master`` (False: the caller places
                :attr:`frame`, e.g. in a paned window).
            start_text: Text of the button that hands a job on with
                ``start=True``.
        """
        self.monitor = monitor
        self.on_pick = on_pick
        self.confirm = confirm or messagebox.askyesno
        self.entries: dict[str, JobEntry] = {}
        self.signature: tuple = ()
        self.message = tk.StringVar(master=master)
        box = ttk.Labelframe(master, text="Shrink jobs (select: fill the form)", padding=4)
        self.frame = box
        if pack:
            box.pack(fill="both", expand=True, pady=(4, 6))
        columns = ("last", "progress", "done", "settings", "job")
        self.jobs = ttk.Treeview(box, columns=columns, show="headings", height=6,
                                 selectmode="extended")
        digit = tkfont.nametofont("TkDefaultFont", root=master).measure("0")
        for name, title, chars, stretch in (("last", "last activity", 18, False),
                                            ("progress", "progress", 28, False),
                                            ("done", "done / to go", 22, False),
                                            ("settings", "settings", 28, False),
                                            ("job", "folder and filters", 40, True)):
            self.jobs.heading(name, text=title, anchor="w")
            self.jobs.column(name, width=chars * digit, stretch=stretch, anchor="w")
        scroll = ttk.Scrollbar(box, orient="vertical", command=self.jobs.yview)
        self.jobs.configure(yscrollcommand=scroll.set)
        self.jobs.pack(side="left", fill="both", expand=True)
        scroll.pack(side="left", fill="y")
        side = ttk.Frame(box)
        side.pack(side="left", fill="y", padx=(6, 0))
        ttk.Button(side, text=start_text, style="Accent.TButton",
                   command=lambda: self.pick(True)).pack(fill="x")
        ttk.Button(side, text="Remove", command=self.remove_selected).pack(fill="x", pady=(6, 0))
        ttk.Button(side, text="Remove finished",
                   command=self.remove_finished).pack(fill="x", pady=(4, 0))
        ttk.Label(side, textvariable=self.message, wraplength=160,
                  style="Hint.TLabel").pack(fill="x", pady=(6, 0))
        self.jobs.bind("<<TreeviewSelect>>", lambda _event: self.pick(False))
        self.jobs.bind("<Double-1>", lambda _event: self.pick(True))

    def refresh(self) -> None:
        """Fill the list again if the job files changed."""
        registry = self.monitor.registry()
        signature = tuple(self.stamp(path) for path in self.monitor.job_files())
        if signature == self.signature:
            return
        self.signature = signature
        selected = self.jobs.selection()
        chosen = self.entries[selected[0]].spec.key if selected else None
        self.jobs.delete(*self.jobs.get_children())
        self.entries = {}
        gib = 1024 ** 3
        for entry in registry.entries():
            settings = self.settings_text(entry.settings)
            item = self.jobs.insert("", "end", values=(
                entry.last, f"{entry.gauge(16)} {entry.progress()}",
                f"{entry.read / gib:.1f} / "
                + (f"{entry.todo_bytes / gib:.1f} GiB" if entry.planned else "? GiB"), settings,
                entry.spec.label()))
            self.entries[item] = entry
            if entry.spec.key == chosen:
                self.jobs.selection_set(item)
                self.jobs.focus(item)
        if not self.jobs.focus() and self.jobs.get_children():
            self.jobs.focus(self.jobs.get_children()[0])

    @staticmethod
    def settings_text(settings: dict[str, object]) -> str:
        """Describe encoder settings briefly.

        Args:
            settings: ``crf``, ``preset``, ``jobs``, ``threads``.

        Returns:
            E.g. ``crf 28, faster, 2 jobs x 11 threads``.
        """
        text = f"crf {settings.get('crf') or 28}"
        if settings.get("preset") not in (None, "", "medium"):
            text += f", {settings['preset']}"
        if settings.get("jobs") not in (None, 1):
            text += f", {settings['jobs']} jobs"
        if settings.get("threads"):
            text += f" x {settings['threads']} threads"
        return text

    @staticmethod
    def stamp(path: str) -> tuple[float, int]:
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

    @staticmethod
    def form_values(entry: JobEntry) -> dict[str, object]:
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
                "newer_than": str(newer or ""),
                "retry_failed": bool(entry.settings.get("retry_failed")),
                "keep_copies": bool(entry.settings.get("keep_copies"))}

    def running_key(self) -> str | None:
        """Return the key of the job that is running now.

        Returns:
            Its :attr:`JobSpec.key`, or None if no shrink runs.
        """
        log_path = self.monitor.control.active_log() \
            if self.monitor.control.active_pid() is not None else None
        found = JobRegistry.from_log(log_path) if log_path else None
        return found[0].key if found else None

    def remove_selected(self) -> int:
        """Forget the selected jobs after asking.

        Returns:
            Number of jobs removed.
        """
        chosen = [self.entries[item] for item in self.jobs.selection() if item in self.entries]
        return self._remove(chosen, "Remove the selected job(s) from the list?")

    def remove_finished(self) -> int:
        """Forget every job with nothing left to do, after asking.

        Returns:
            Number of jobs removed.
        """
        done = [entry for entry in self.entries.values()
                if entry.planned and entry.todo_bytes == 0]
        return self._remove(done, "Remove every finished job from the list?")

    def _remove(self, chosen: list[JobEntry], question: str) -> int:
        """Remove jobs except the running one, after a yes.

        Args:
            chosen: Jobs to remove.
            question: Question for the confirmation.

        Returns:
            Number of jobs removed.
        """
        running = self.running_key()
        keep = [entry for entry in chosen if entry.spec.key == running]
        chosen = [entry for entry in chosen if entry.spec.key != running]
        if not chosen:
            self.message.set("the running job stays" if keep else "nothing to remove")
            return 0
        names = "\n".join(entry.spec.label() for entry in chosen[:8])
        more = f"\n... and {len(chosen) - 8} more" if len(chosen) > 8 else ""
        if not self.confirm("Remove jobs", f"{question}\n\n{names}{more}\n\nOnly the list "
                            "entries go; databases, ledger and videos stay."):
            return 0
        count = self.monitor.registry().remove([entry.spec.key for entry in chosen])
        self.message.set(f"{count} job(s) removed" + (" - the running job stays" if keep else ""))
        self.signature = ()
        self.refresh()
        return count

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
