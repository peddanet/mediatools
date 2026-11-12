"""Editor of the configuration file ``mediatools.ini``."""
from __future__ import annotations

import configparser
import os
import tkinter as tk
from tkinter import scrolledtext, ttk

from mediatools.core.config import Config
from mediatools.gui.opener import Opener


class ConfigPanel:
    """Edits the INI file with its comments; shows the effective values.

    The left area holds the file as it is (comments included); *Save*
    checks it with the same INI parser that mediatools uses and writes it
    only if it can be read. The right area shows the effective values,
    i.e. the file plus the defaults of all keys it does not set.

    Attributes:
        frame: Container of the widgets (place it in the parent).
        config_factory: Creates a :class:`Config` (replaceable in tests).
        opener: Opens the folder of the file.
        editor: Text of the file.
        effective: Effective values (read-only).
        message: Result of the last button.
        path: Location of the file.
    """

    def __init__(self, master: tk.Misc, config_factory=Config, opener: Opener | None = None):
        """Build the editor and load the file.

        Args:
            master: Parent widget.
            config_factory: Callable without arguments that returns a :class:`Config`.
            opener: Opener for the folder; ``xdg-open`` if None.
        """
        self.frame = ttk.Frame(master)
        self.config_factory = config_factory
        self.opener = opener or Opener()
        self.path = tk.StringVar()
        self.message = tk.StringVar()
        ttk.Label(self.frame, textvariable=self.path, font="TkFixedFont").pack(fill="x")
        toolbar = ttk.Frame(self.frame)
        toolbar.pack(fill="x", pady=4)
        ttk.Button(toolbar, text="Save", command=self.save).pack(side="left")
        ttk.Button(toolbar, text="Reload", command=self.load).pack(side="left", padx=4)
        ttk.Button(toolbar, text="Create from template", command=self.create).pack(side="left")
        ttk.Button(toolbar, text="Open folder", command=self.open_folder).pack(side="left", padx=4)
        ttk.Label(toolbar, textvariable=self.message).pack(side="left", padx=8)
        panes = ttk.PanedWindow(self.frame, orient="horizontal")
        panes.pack(fill="both", expand=True)
        self.editor = scrolledtext.ScrolledText(panes, font="TkFixedFont", undo=True)
        self.effective = scrolledtext.ScrolledText(panes, font="TkFixedFont", state="disabled")
        panes.add(self.editor, weight=3)
        panes.add(self.effective, weight=2)
        self.load()

    def load(self) -> None:
        """Show the file (or the template if it is missing) and the effective values."""
        config = self.config_factory()
        self.path.set(config.path + ("" if config.exists() else
                                     "   (missing - the template is shown; Save creates it)"))
        if config.exists():
            with open(config.path, encoding="utf-8") as handle:
                text = handle.read()
        else:
            text = config.TEMPLATE
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", text)
        self.effective.configure(state="normal")
        self.effective.delete("1.0", "end")
        self.effective.insert("1.0", "# effective values (file + defaults)\n\n" + config.render())
        self.effective.configure(state="disabled")

    def save(self) -> bool:
        """Check the edited text and write it.

        Returns:
            True if the file was written.
        """
        text = self.editor.get("1.0", "end-1c")
        try:
            configparser.ConfigParser(interpolation=None).read_string(text)
        except configparser.Error as exc:
            self.message.set("not saved: " + str(exc).splitlines()[0])
            return False
        config = self.config_factory()
        os.makedirs(os.path.dirname(config.path), exist_ok=True)
        tmp = config.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(text if text.endswith("\n") else text + "\n")
        os.replace(tmp, config.path)
        self.load()
        self.message.set("saved " + config.path)
        return True

    def create(self) -> None:
        """Write the commented template if the file does not exist."""
        config = self.config_factory()
        written = config.write_template()
        self.load()
        self.message.set(("created " if written else "already exists, not changed: ")
                         + config.path)

    def open_folder(self) -> None:
        """Open the folder of the file."""
        self.message.set(self.opener.open(os.path.dirname(self.config_factory().path)))
