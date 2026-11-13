"""Bar that offers a restart when a newer release is installed."""
from __future__ import annotations

import threading
import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

from mediatools import __version__
from mediatools.core.selfrestart import SelfRestart


class UpdateBar:
    """Notices a newly installed release and offers to restart the window.

    ``mediatools update`` installs a release while the GUI keeps running
    the old code. Every :attr:`seconds` the bar asks the installed release
    for its version (``python -m mediatools --version``, in a background
    thread so the window never blocks); if it differs from the running one,
    the bar appears above the tabs with a *Restart now* button.

    Attributes:
        frame: The bar (shown only when needed).
        text: Text of the bar.
        installed: Version of the installed release, as last checked.
        running: Version of the running window.
        version_of: Returns the installed version (replaceable in tests).
    """

    def __init__(self, root: tk.Misc, on_restart: Callable[[], object],
                 version_of: Callable[[], str | None] | None = None):
        """Build the (hidden) bar.

        Args:
            root: Main window.
            on_restart: Called by *Restart now*.
            version_of: Returns the installed version; asks the installed
                release if None.
        """
        self.frame = ttk.Frame(root, padding=(8, 4))
        self.text = tk.StringVar()
        ttk.Label(self.frame, textvariable=self.text).pack(side="left")
        ttk.Button(self.frame, text="Restart now", style="Accent.TButton",
                   command=on_restart).pack(side="left", padx=8)
        self.running = __version__
        self.installed = __version__
        self.version_of = version_of or (lambda: SelfRestart([], "").installed_version())

    def check(self, background: bool = True) -> None:
        """Ask for the installed version.

        Args:
            background: Ask in a thread (the window stays responsive).
        """
        def ask() -> None:
            self.installed = self.version_of() or self.installed

        if background:
            threading.Thread(target=ask, daemon=True).start()
        else:
            ask()

    def show(self, before: tk.Misc) -> bool:
        """Show the bar above a widget if a newer release is installed.

        Args:
            before: Widget the bar is placed above.

        Returns:
            True if the bar is (now) shown.
        """
        if self.installed == self.running:
            return False
        self.text.set(f"mediatools {self.installed} is installed; this window runs "
                      f"{self.running}.")
        if not self.frame.winfo_ismapped():
            self.frame.pack(fill="x", before=before)
        return True
