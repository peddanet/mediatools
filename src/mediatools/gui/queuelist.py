"""The shrink queue in processing order: running job first, then the waiting ones."""
from __future__ import annotations

import argparse
import os
import shlex
import subprocess  # nosec B404 - starts mediatools itself with a fixed argument list
import sys
import tkinter as tk
from collections.abc import Callable
from tkinter import font as tkfont
from tkinter import messagebox, ttk

from mediatools.core.mediatypes import MediaTypes
from mediatools.gui.joblist import JobList
from mediatools.gui.shrinkmonitor import ShrinkMonitor
from mediatools.shrink.jobregistry import JobEntry, JobRegistry
from mediatools.shrink.jobspec import JobSpec
from mediatools.shrink.shrinkqueue import ShrinkQueue


class QueueList:
    """Shows the running shrink job and the queue, in the order of processing.

    The first row is the job that runs now (``running``), the others are
    the waiting jobs (``1``, ``2`` ...); a job that was stopped or aborted
    is back among them as ``1 paused`` (:meth:`ShrinkQueue.put_back`) and
    goes on with *Continue* or leaves with *Remove*. Each row shows the progress of
    that job as far as it is known (its remembered job with the same folder
    and filters), the figures in GiB, the settings and the folder with its
    filters.

    Buttons: *Continue* starts the selected waiting job now (it becomes
    the first one) when no shrink runs; *Stop, then next* asks a running
    shrink to stop after its current file and then start the selected
    waiting job (or the first); *Add* opens the job dialog for a
    new job; *Change* opens it with the selected waiting job; *Remove*
    takes the selected waiting jobs off the queue (after a question);
    *Up* and *Down* change the order.

    Attributes:
        frame: The box with list and buttons (placed by the caller).
        jobs: The list widget.
        monitor: Reader of the running job and of the job registry.
        queue: The shrink queue.
        parse: Parses ``shrink`` arguments (argparse namespace) or raises.
        open_dialog: Opens the job dialog: ``(position or None, args or None)``.
        confirm: Yes/no question (title, text).
        launcher: Starts ``mediatools`` with arguments in the background.
        message: Result of the last button.
        signature: State of queue, registry and run when last filled.
    """

    def __init__(self, master: tk.Misc, monitor: ShrinkMonitor,
                 parse: Callable[[list[str]], argparse.Namespace],
                 open_dialog: Callable[[int | None, list[str] | None], object] | None = None,
                 **extra) -> None:
        """Build the list.

        Args:
            master: Parent widget.
            monitor: Reader of the running job and the registry.
            parse: Parses ``shrink`` arguments.
            open_dialog: Opens the job dialog; nothing if None.
            **extra: ``queue`` (:class:`ShrinkQueue`, default the user's),
                ``confirm`` (yes/no question, default a message box) and
                ``launcher`` (default a detached process).
        """
        self.monitor = monitor
        self.parse = parse
        self.open_dialog = open_dialog
        self.queue: ShrinkQueue = extra.get("queue") or ShrinkQueue.default()
        self.confirm: Callable[[str, str], bool] = extra.get("confirm") or messagebox.askyesno
        self.launcher: Callable[[list[str]], object] = extra.get("launcher") or self.launch
        self.signature: tuple = ()
        self.message = tk.StringVar(master=master)
        self.frame = ttk.Labelframe(master, text="Shrink queue (in the order of processing)",
                                    padding=4)
        columns = ("state", "progress", "done", "settings", "job")
        self.jobs = ttk.Treeview(self.frame, columns=columns, show="headings", height=6,
                                 selectmode="extended")
        digit = tkfont.nametofont("TkDefaultFont", root=master).measure("0")
        for name, title, chars, stretch in (("state", "#", 9, False),
                                            ("progress", "progress", 28, False),
                                            ("done", "done / to go", 22, False),
                                            ("settings", "settings", 28, False),
                                            ("job", "folder and options", 40, True)):
            self.jobs.heading(name, text=title, anchor="w")
            self.jobs.column(name, width=chars * digit, stretch=stretch, anchor="w")
        scroll = ttk.Scrollbar(self.frame, orient="vertical", command=self.jobs.yview)
        self.jobs.configure(yscrollcommand=scroll.set)
        self.jobs.pack(side="left", fill="both", expand=True)
        scroll.pack(side="left", fill="y")
        side = ttk.Frame(self.frame)
        side.pack(side="left", fill="y", padx=(6, 0))
        ttk.Button(side, text="Continue", style="Accent.TButton",
                   command=self.start).pack(fill="x")
        ttk.Button(side, text="Stop, then next", command=self.stop_then_next).pack(
            fill="x", pady=(4, 0))
        for text, command in (("Add", self.add), ("Change", self.change),
                              ("Remove", self.remove)):
            ttk.Button(side, text=text, command=command).pack(fill="x", pady=(4, 0))
        arrows = ttk.Frame(side)
        arrows.pack(fill="x", pady=(4, 0))
        ttk.Button(arrows, text="Up", width=5, command=lambda: self.shift(-1)).pack(
            side="left", expand=True, fill="x")
        ttk.Button(arrows, text="Down", width=5, command=lambda: self.shift(1)).pack(
            side="left", expand=True, fill="x")
        ttk.Label(side, textvariable=self.message, wraplength=160,
                  style="Hint.TLabel").pack(fill="x", pady=(6, 0))
        self.jobs.bind("<Double-1>", lambda _event: self.change())

    # -- content -----------------------------------------------------------
    def spec_of(self, args: list[str]) -> tuple[JobSpec, dict[str, object]] | None:
        """Turn queued ``shrink`` arguments into job and settings.

        Args:
            args: Arguments, folder first.

        Returns:
            ``(spec, settings)``, or None if they cannot be parsed.
        """
        try:
            found = self.parse(list(args))
        except (SystemExit, ValueError):
            return None
        spec = JobSpec(os.path.abspath(found.path or ""), MediaTypes.parse_extensions(found.ext),
                       found.min_seconds or 0.0, found.min_size or 0, found.newer_than)
        settings = {"crf": found.crf, "preset": found.preset, "jobs": found.jobs,
                    "threads": found.threads}
        return spec, settings

    @staticmethod
    def figures(entry: JobEntry | None) -> tuple[str, str]:
        """Return progress and GiB figures of a remembered job.

        Args:
            entry: The job, or None if it never ran.

        Returns:
            ``(progress, done / to go)``.
        """
        if entry is None:
            return "new", "-"
        gib = 1024 ** 3
        todo = f"{entry.todo_bytes / gib:.1f} GiB" if entry.planned else "? GiB"
        return f"{entry.gauge(16)} {entry.progress()}", f"{entry.read / gib:.1f} / {todo}"

    def rows(self) -> list[tuple[str, tuple[str, ...]]]:
        """Build the rows: the running job, then the waiting ones.

        Returns:
            ``(id, values)`` per row; the id is ``running`` or the position.
        """
        self.queue.recover()
        known = {entry.spec.key: entry for entry in self.monitor.registry().entries()}
        result: list[tuple[str, tuple[str, ...]]] = []
        if self.monitor.control.active_pid() is not None:
            found = JobRegistry.from_log(self.monitor.control.active_log() or "")
            if found is not None:
                entry = known.get(found[0].key) or JobEntry(found[0], found[1])
                result.append(("running", ("running", *self.figures(entry),
                                           JobList.settings_text(entry.settings),
                                           found[0].label())))
        for number, queued in enumerate(self.queue.entries(), 1):
            parsed = self.spec_of(queued["args"])
            place = f"{number} paused" if queued.get("paused") else str(number)
            if parsed is None:
                result.append((str(number), (place, "?", "-", "-",
                                             shlex.join(queued["args"]))))
                continue
            note = f"   ({queued['note']})" if queued.get("note") else ""
            result.append((str(number), (place, *self.figures(known.get(parsed[0].key)),
                                         JobList.settings_text(parsed[1]),
                                         parsed[0].label() + note)))
        return result

    def refresh(self) -> None:
        """Fill the list again if the queue, the job files or the run changed."""
        files = [self.queue.path, *self.monitor.job_files()]
        stamps = tuple(JobList.stamp(path) for path in files)
        signature = (stamps, self.monitor.control.active_pid())
        if signature == self.signature:
            return
        self.signature = signature
        chosen = set(self.jobs.selection())
        self.jobs.delete(*self.jobs.get_children())
        for row_id, values in self.rows():
            self.jobs.insert("", "end", iid=row_id, values=values)
            if row_id in chosen:
                self.jobs.selection_set(row_id)
        if not self.jobs.focus() and self.jobs.get_children():
            self.jobs.focus(self.jobs.get_children()[0])

    def reload(self) -> None:
        """Fill the list now, whatever changed."""
        self.signature = ()
        self.refresh()

    # -- buttons -------------------------------------------------------------
    def selected(self) -> list[int]:
        """Return the positions of the selected waiting jobs.

        Returns:
            Positions (1 = next), ascending; the running row is left out.
        """
        return sorted(int(item) for item in self.jobs.selection() if item.isdigit())

    def start(self) -> bool:
        """*Continue*: start the selected waiting job (or the first) now.

        Returns:
            True if ``mediatools queue --run`` was started.
        """
        if self.monitor.control.active_pid() is not None:
            self.message.set("a shrink runs - the next job starts when it ends")
            return False
        if not self.queue.entries():
            self.message.set("the queue is empty - Add puts a job into it")
            return False
        chosen = self.selected()
        if chosen and chosen[0] != 1:
            self.queue.move(chosen[0], 1)
        self.launcher(["queue", "--run"])
        self.message.set("started")
        self.reload()
        return True

    def stop_then_next(self) -> bool:
        """*Stop, then next*: stop the running job, then start the selected one.

        Returns:
            True if the stop was requested.
        """
        if self.monitor.control.active_pid() is None:
            self.message.set("no shrink runs - Continue starts a job")
            return False
        if not self.queue.entries():
            self.message.set("the queue is empty - Add puts a job into it")
            return False
        chosen = self.selected()
        if chosen and chosen[0] != 1:
            self.queue.move(chosen[0], 1)
        self.queue.next_after_stop()
        self.monitor.control.request_stop()
        self.message.set("stopping after the current file, then the next job starts")
        self.reload()
        return True

    def add(self) -> object:
        """*Add*: open the job dialog for a new job.

        Returns:
            Result of the dialog opener, or None without one.
        """
        return self.open_dialog(None, None) if self.open_dialog else None

    def change(self) -> object:
        """*Change*: open the job dialog with the selected waiting job.

        Returns:
            Result of the dialog opener, or None.
        """
        chosen = self.selected()
        if len(chosen) != 1:
            self.message.set("select one waiting job to change")
            return None
        entries = self.queue.entries()
        if self.open_dialog is None or chosen[0] > len(entries):
            return None
        return self.open_dialog(chosen[0], entries[chosen[0] - 1]["args"])

    def remove(self) -> int:
        """*Remove*: take the selected waiting jobs off the queue, after asking.

        Returns:
            Number of jobs removed.
        """
        chosen = self.selected()
        if not chosen:
            self.message.set("select the waiting job(s) to remove"
                             + (" - the running job is stopped with 'Stop'"
                                if "running" in self.jobs.selection() else ""))
            return 0
        if not self.confirm("Remove from the queue",
                            f"Remove {len(chosen)} waiting job(s) from the queue?"):
            return 0
        for number in reversed(chosen):
            self.queue.remove(number)
        self.message.set(f"{len(chosen)} job(s) removed")
        self.reload()
        return len(chosen)

    def shift(self, step: int) -> None:
        """Move the selected waiting job one place up (-1) or down (+1).

        Args:
            step: -1 or +1.
        """
        chosen = self.selected()
        if len(chosen) != 1:
            self.message.set("select one waiting job to move")
            return
        target = chosen[0] + step
        if not 1 <= target <= len(self.queue.entries()):
            return
        self.queue.move(chosen[0], target)
        self.reload()
        self.jobs.selection_set(str(target))

    @staticmethod
    def launch(args: list[str]) -> subprocess.Popen[bytes]:
        """Start mediatools in its own session, detached from the GUI.

        Args:
            args: Arguments after ``mediatools``.

        Returns:
            The process.
        """
        return subprocess.Popen(  # pylint: disable=consider-using-with  # nosec B603
            [sys.executable, "-m", "mediatools", *args], stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
