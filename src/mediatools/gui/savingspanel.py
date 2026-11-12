"""Table of the space freed by shrinking, per session or per day."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from mediatools.core.datadir import DataDir
from mediatools.shrink.savingslog import SavingsLog


class SavingsPanel:
    """Shows ``mediatools savings`` as a table: sessions or days, and the total.

    A session is one shrink log, from ``mediatools shrink`` until the run
    ends (a release switch with ``reload`` continues the session). The
    sizes come from the result tables of the logs.

    Attributes:
        frame: Container of the widgets (place it in the parent).
        logs: Reader of the shrink logs.
        by: ``session`` or ``day``.
        tree: The table.
        total: Text of the total line.
    """

    COLUMNS = (("from", 60), ("to", 60), ("files", 70), ("read GiB", 90),
               ("written GiB", 100), ("freed GiB", 90), ("output", 80))

    def __init__(self, master: tk.Misc, logs: SavingsLog | None = None):
        """Build the table and fill it.

        Args:
            master: Parent widget.
            logs: Reader of the shrink logs; the data folder's logs if None.
        """
        self.frame = ttk.Frame(master)
        self.logs = logs or SavingsLog(DataDir().sub("logs"))
        self.by = tk.StringVar(value="session")
        self.total = tk.StringVar()
        toolbar = ttk.Frame(self.frame)
        toolbar.pack(fill="x", pady=(0, 4))
        for value, text in (("session", "per session"), ("day", "per day")):
            ttk.Radiobutton(toolbar, text=text, value=value, variable=self.by,
                            command=self.refresh).pack(side="left", padx=(0, 8))
        ttk.Button(toolbar, text="Refresh", command=self.refresh).pack(side="left")
        self.tree = ttk.Treeview(self.frame, columns=[name for name, _ in self.COLUMNS])
        self.tree.heading("#0", text="session")
        self.tree.column("#0", width=170)
        for name, width in self.COLUMNS:
            self.tree.heading(name, text=name)
            self.tree.column(name, width=width, anchor="e")
        self.tree.pack(fill="both", expand=True)
        ttk.Label(self.frame, textvariable=self.total, font="TkFixedFont").pack(fill="x", pady=4)
        ttk.Label(self.frame, foreground="gray30",
                  text="Sizes from the result tables of the shrink logs (MB, rounded); only "
                       "files with status 'shrunk' free space.").pack(fill="x")
        self.refresh()

    def refresh(self) -> None:
        """Read the logs again and fill the table, newest first."""
        by = self.by.get()
        items = self.logs.sessions() if by == "session" else self.logs.days()
        self.tree.heading("#0", text=by)
        self.tree.delete(*self.tree.get_children())
        gib = SavingsLog.gib
        for item in reversed(items):
            label = item.start.strftime("%Y-%m-%d %H:%M") if by == "session" else item.name
            self.tree.insert("", "end", text=label, values=(
                f"{item.start:%H:%M}", f"{item.end:%H:%M}", item.files, gib(item.src),
                gib(item.out), gib(item.freed), f"{item.share:.1f} %"))
        total = self.logs.total()
        self.total.set(f"total {total.name}: {total.files} files, {gib(total.src)} GiB read -> "
                       f"{gib(total.out)} GiB written = {gib(total.freed)} GiB freed")
