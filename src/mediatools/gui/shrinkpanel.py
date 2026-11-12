"""Dashboard of the shrink run: state, progress, space, log, stop and reload."""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import scrolledtext, ttk

from mediatools.gui.opener import Opener
from mediatools.gui.shrinkmonitor import ShrinkMonitor


class ShrinkPanel:
    """Shows the running shrink like ``status`` and ``attach`` together.

    It refreshes itself every :attr:`refresh_ms` milliseconds: process and
    release (``mediatools status``), the progress lines with a progress toolbar,
    the space read, written and freed in this session and in all sessions,
    and the end of the log.
    *Stop* and *Reload* make the same requests as ``mediatools stop`` and
    ``mediatools reload``; the run itself continues independently of the
    GUI, which may be closed at any time.

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
    """

    def __init__(self, master: tk.Misc, monitor: ShrinkMonitor | None = None,
                 opener: Opener | None = None, refresh_ms: int = 2000):
        """Build the dashboard and start refreshing.

        Args:
            master: Parent widget.
            monitor: State reader; one for ``shrink`` if None.
            opener: Opener for the log file; ``xdg-open`` if None.
            refresh_ms: Refresh interval in milliseconds (0: no automatic refresh).
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
        ttk.Button(buttons, text="Reload installed release",
                   command=self.reload).pack(side="left", padx=4)
        ttk.Button(buttons, text="Refresh", command=self.refresh).pack(side="left")
        ttk.Button(buttons, text="Open log", command=self.open_log).pack(side="left", padx=4)
        ttk.Label(buttons, textvariable=self.message).pack(side="left", padx=8)
        self.log = scrolledtext.ScrolledText(self.frame, height=6, font="TkFixedFont",
                                             wrap="none", state="disabled")
        self.log.pack(fill="both", expand=True)
        self.refresh()

    def refresh(self) -> None:
        """Read the state and show it; schedule the next refresh."""
        state = self.monitor.read()
        self.report.set("\n".join(state.report))
        self.progress.set("\n".join(state.progress) if state.progress
                          else "(no progress lines - no shrink is encoding right now)")
        self.overall.set(state.overall)
        self.meter["value"] = state.percent or 0.0
        self.log_path = state.log_path
        at_end = self.log.yview()[1] >= 0.999
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.insert("1.0", "\n".join(self.monitor.tail(state.log_path)))
        if at_end:
            self.log.see("end")
        self.log.configure(state="disabled")
        if self.refresh_ms:
            self.frame.after(self.refresh_ms, self.refresh)

    def stop(self) -> None:
        """Request a stop after the current file."""
        self.message.set(self.monitor.stop())

    def reload(self) -> None:
        """Request a switch to the installed release."""
        self.message.set(self.monitor.reload())

    def open_log(self) -> None:
        """Open the log of the run in the desktop's viewer."""
        if not self.log_path or not os.path.exists(self.log_path):
            self.message.set("no log of a running shrink")
            return
        self.message.set(self.opener.open(self.log_path))
