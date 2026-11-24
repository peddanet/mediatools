"""Dashboard of the shrink run: state, progress, space, log, stop and reload."""
from __future__ import annotations

import argparse
import os
import tkinter as tk
from collections.abc import Callable
from tkinter import scrolledtext, ttk

from mediatools.gui.opener import Opener
from mediatools.gui.queuelist import QueueList
from mediatools.gui.shrinkmonitor import ShrinkMonitor


class ShrinkPanel:
    """Shows the running shrink like ``status`` and ``attach`` together.

    It refreshes itself every :attr:`refresh_ms` milliseconds: process and
    release (``mediatools status``), the progress lines with a progress toolbar,
    the space read, written and freed in this session and in all sessions,
    and the end of the log.
    *Stop after current file*, *Abort now* (after a question) and *Reload*
    make the same requests as ``mediatools stop``, ``mediatools stop --now``
    and ``mediatools reload``; a stopped or aborted job from the queue goes
    back to the queue, paused; the run itself continues independently of the
    GUI, which may be closed at any time.

    Below, the shrink queue follows in the order of processing - the
    running job first (:class:`QueueList`) - and the end of the log.

    The three parts - status, job list, log - are separate widgets
    (:attr:`parts`). Placed in a vertical :class:`ttk.PanedWindow` they get
    their own sashes, so each can be made larger or smaller with the mouse;
    in any other parent they are simply stacked.

    Attributes:
        frame: Container of the widgets (place it in the parent).
        monitor: Reads the state of the run.
        opener: Opens the log in the desktop's text viewer.
        refresh_ms: Refresh interval in milliseconds.
        report: Text of the status report.
        progress: Text of the progress lines.
        overall: Text of the overall space line.
        meter: Progress toolbar (percent of the run).
        log: Area with the end of the log.
        message: Result of the last button.
        log_path: Log file of the shown run, or None.
        joblist: The shrink queue (:class:`QueueList`).
    """

    def __init__(self, master: tk.Misc, monitor: ShrinkMonitor | None = None,
                 opener: Opener | None = None, refresh_ms: int = 2000,
                 parse: Callable[[list[str]], argparse.Namespace] | None = None,
                 open_dialog: Callable[[int | None, list[str] | None], object] | None = None):
        """Build the dashboard and start refreshing.

        Args:
            master: Parent widget.
            monitor: State reader; one for ``shrink`` if None.
            opener: Opener for the log file; ``xdg-open`` if None.
            refresh_ms: Refresh interval in milliseconds (0: no automatic refresh).
            parse: Parses ``shrink`` arguments (shows queued jobs with
                their figures); without it they are shown as text.
            open_dialog: Opens the job dialog for *Add* and *Change*.
        """
        self.frame = ttk.Frame(master)
        self.monitor = monitor or ShrinkMonitor()
        self.opener = opener or Opener()
        self.refresh_ms = refresh_ms
        self.log_path: str | None = None
        self.report = tk.StringVar()
        self.progress = tk.StringVar()
        self.overall = tk.StringVar()
        self.message = tk.StringVar()
        ttk.Label(self.frame, textvariable=self.report, font="TkFixedFont",
                  justify="left").pack(fill="x")
        self.meter = ttk.Progressbar(self.frame, maximum=100.0)
        self.meter.pack(fill="x", pady=4)
        ttk.Label(self.frame, textvariable=self.progress, font="TkFixedFont",
                  justify="left").pack(fill="x")
        ttk.Label(self.frame, textvariable=self.overall,
                  font="TkFixedFont").pack(fill="x", pady=(4, 0))
        buttons = ttk.Frame(self.frame)
        buttons.pack(fill="x", pady=4)
        ttk.Button(buttons, text="Stop after current file", command=self.stop).pack(side="left")
        ttk.Button(buttons, text="Abort now", command=self.abort).pack(side="left", padx=(4, 0))
        ttk.Button(buttons, text="Reload installed release",
                   command=self.reload).pack(side="left", padx=4)
        ttk.Button(buttons, text="Refresh", command=self.refresh).pack(side="left")
        ttk.Button(buttons, text="Open log", command=self.open_log).pack(side="left", padx=4)
        ttk.Label(buttons, textvariable=self.message).pack(side="left", padx=8)
        self.joblist = QueueList(master, self.monitor, parse or self.unparsed, open_dialog)
        self.log = scrolledtext.ScrolledText(master, height=4, font="TkFixedFont",
                                             wrap="none", state="disabled")
        if not isinstance(master, ttk.PanedWindow):
            for widget, weight in self.parts:
                widget.pack(fill="both", expand=bool(weight))
        self.refresh()

    def refresh(self) -> None:
        """Read the state and show it; schedule the next refresh."""
        state = self.monitor.read()
        self.report.set("\n".join(state.report + [
            state.job or "job:     no shrink is running - Continue starts the first queued "
                         "job, Add puts one in"]))
        self.progress.set("\n".join(state.progress) if state.progress
                          else "(no progress lines - no shrink is encoding right now)")
        self.overall.set(state.overall)
        self.meter["value"] = state.percent or 0.0
        self.log_path = state.log_path
        self.show_log("\n".join(self.monitor.tail(state.log_path)))
        self.joblist.refresh()
        if self.refresh_ms:
            self.frame.after(self.refresh_ms, self.refresh)

    @property
    def parts(self) -> list[tuple[tk.Widget, int]]:
        """Status, queue and log with their weights, for a paned window."""
        return [(self.frame, 0), (self.joblist.frame, 1), (self.log, 1)]

    @staticmethod
    def unparsed(args: list[str]) -> argparse.Namespace:
        """Parser stand-in when none is given: every queued job is shown as text.

        Args:
            args: Arguments of a queued job.

        Raises:
            ValueError: Always.
        """
        raise ValueError(f"no parser for {args}")

    def stop(self) -> None:
        """Request a stop after the current file (the queue does not go on)."""
        self.joblist.queue.next_after_stop(False)
        self.message.set(self.monitor.stop())

    def abort(self) -> None:
        """Abort the current file at once, after a question (the queue does not go on).

        The encode time of the current file is lost; the job stays in the
        queue, paused, if it came from there.
        """
        if not self.joblist.confirm("Abort now",
                            "Abort the current file now?\n\nIts partial output is deleted, "
                            "the original stays untouched; the time spent on this file is "
                            "lost. A job from the queue stays in the queue, paused."):
            return
        self.joblist.queue.next_after_stop(False)
        self.message.set(self.monitor.abort())

    def reload(self) -> None:
        """Request a switch to the installed release."""
        self.message.set(self.monitor.reload())

    def show_log(self, text: str) -> None:
        """Show the end of the log without taking the scroll position away.

        The text is replaced only when it changed. If the view showed the
        end, it follows the new end; otherwise it keeps its position, so the
        log can be read while the run goes on.

        Args:
            text: Last lines of the log.
        """
        if self.log.get("1.0", "end-1c") == text:
            return
        first, last = self.log.yview()
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.insert("1.0", text)
        if last >= 0.999:
            self.log.see("end")
        else:
            self.log.yview_moveto(first)
        self.log.configure(state="disabled")

    def open_log(self) -> None:
        """Open the log of the run in the desktop's viewer."""
        if not self.log_path or not os.path.exists(self.log_path):
            self.message.set("no log of a running shrink")
            return
        self.message.set(self.opener.open(self.log_path))
