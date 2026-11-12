"""Browser of the files mediatools writes: logs, reports, plans, state."""
from __future__ import annotations

import os
import time
import tkinter as tk
from tkinter import scrolledtext, ttk
from typing import Callable

from mediatools.core.datadir import DataDir
from mediatools.gui.opener import Opener


class FilesPanel:
    """Lists the files below the data folder and shows the selected one.

    Folders: ``logs`` (one log per run), ``reports`` (progress lists, sync
    reports), ``plans`` (flatten plans, usable for undo) and ``state``.
    The newest files come first. Large files are shown from their end.

    Attributes:
        frame: Container of the widgets (place it in the parent).
        root: Data folder.
        opener: Opens files and folders.
        on_undo: Called with a plan file for *Use as flatten undo plan*, or None.
        tree: List of folders and files.
        viewer: Content of the selected file.
        message: Result of the last button.
    """

    FOLDERS = ("logs", "reports", "plans", "state")
    SHOW_BYTES = 512 * 1024

    def __init__(self, master: tk.Misc, root: str | None = None, opener: Opener | None = None,
                 on_undo: Callable[[str], None] | None = None):
        """Build the browser and list the files.

        Args:
            master: Parent widget.
            root: Data folder; the mediatools data folder if None.
            opener: Opener for files and folders; ``xdg-open`` if None.
            on_undo: Called with a plan file for an undo, or None.
        """
        self.frame = ttk.Frame(master)
        self.root = root or DataDir().root
        self.opener = opener or Opener()
        self.on_undo = on_undo
        self.message = tk.StringVar()
        toolbar = ttk.Frame(self.frame)
        toolbar.pack(fill="x", pady=(0, 4))
        ttk.Button(toolbar, text="Refresh", command=self.refresh).pack(side="left")
        ttk.Button(toolbar, text="Open", command=self.open_selected).pack(side="left", padx=4)
        ttk.Button(toolbar, text="Open data folder",
                   command=lambda: self.message.set(self.opener.open(self.root))).pack(side="left")
        ttk.Button(toolbar, text="Use as flatten undo plan",
                   command=self.use_as_undo).pack(side="left", padx=4)
        ttk.Label(toolbar, textvariable=self.message).pack(side="left", padx=8)
        panes = ttk.PanedWindow(self.frame, orient="horizontal")
        panes.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(panes, columns=("size", "modified"), height=20)
        self.tree.heading("#0", text="file")
        self.tree.heading("size", text="size")
        self.tree.heading("modified", text="modified")
        self.tree.column("size", width=90, anchor="e")
        self.tree.column("modified", width=140)
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self.show_selected())
        self.viewer = scrolledtext.ScrolledText(panes, font="TkFixedFont", wrap="none",
                                                state="disabled")
        panes.add(self.tree, weight=2)
        panes.add(self.viewer, weight=3)
        self.refresh()

    @staticmethod
    def size_text(size: int) -> str:
        """Format a file size.

        Args:
            size: Bytes.

        Returns:
            Text such as ``1.5 MiB``.
        """
        value = float(size)
        for unit in ("B", "KiB", "MiB", "GiB"):
            if value < 1024 or unit == "GiB":
                return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
            value /= 1024
        return f"{value:.1f} GiB"                   # not reached

    def refresh(self) -> None:
        """List the folders and their files, newest first."""
        self.tree.delete(*self.tree.get_children())
        for folder in self.FOLDERS:
            path = os.path.join(self.root, folder)
            node = self.tree.insert("", "end", iid=path, text=folder + "/", open=folder == "logs")
            try:
                entries = [entry for entry in os.scandir(path) if entry.is_file()]
            except OSError:
                continue
            entries.sort(key=lambda entry: entry.stat().st_mtime, reverse=True)
            for entry in entries:
                info = entry.stat()
                self.tree.insert(node, "end", iid=entry.path, text=entry.name,
                                 values=(self.size_text(info.st_size),
                                         time.strftime("%Y-%m-%d %H:%M",
                                                       time.localtime(info.st_mtime))))

    def selected(self) -> str | None:
        """Return the selected path.

        Returns:
            File or folder path, or None if nothing is selected.
        """
        chosen = self.tree.selection()
        return chosen[0] if chosen else None

    def show_selected(self) -> None:
        """Show the selected file (its end if it is large)."""
        path = self.selected()
        if not path or not os.path.isfile(path):
            return
        try:
            with open(path, "rb") as handle:
                size = handle.seek(0, os.SEEK_END)
                handle.seek(max(0, size - self.SHOW_BYTES))
                data = handle.read()
        except OSError as exc:
            data = str(exc).encode()
            size = 0
        text = data.decode("utf-8", errors="replace")
        if size > self.SHOW_BYTES:
            text = f"[showing the last {self.SHOW_BYTES // 1024} KiB of {size} bytes]\n" + text
        self.viewer.configure(state="normal")
        self.viewer.delete("1.0", "end")
        self.viewer.insert("1.0", text)
        self.viewer.configure(state="disabled")

    def open_selected(self) -> None:
        """Open the selected file or folder with the desktop's application."""
        path = self.selected()
        self.message.set(self.opener.open(path) if path else "nothing selected")

    def use_as_undo(self) -> None:
        """Hand the selected plan file to the flatten form for an undo."""
        path = self.selected()
        if not path or not os.path.isfile(path) \
                or os.path.dirname(path) != os.path.join(self.root, "plans"):
            self.message.set("select a plan file in plans/")
            return
        if self.on_undo is not None:
            self.on_undo(path)
            self.message.set("plan set in the flatten tab: " + os.path.basename(path))
