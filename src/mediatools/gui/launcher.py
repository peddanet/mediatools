"""Starts the GUI for ``mediatools gui`` after checking its requirements."""
from __future__ import annotations

import argparse
import importlib
import os

from mediatools.gui.catalog import CommandCatalog
from mediatools.gui.desktopentry import DesktopEntry


class GuiLauncher:
    """Checks display and Tk, then opens the main window.

    Tk is part of Python, but Debian and Ubuntu ship it as a separate
    package (``python3-tk``); without it ``import tkinter`` fails. The
    launcher reports that with the command that installs it instead of a
    traceback. Without a graphical session (``DISPLAY`` or
    ``WAYLAND_DISPLAY``) no window can open, e.g. over SSH.

    Attributes:
        parser: Argument parser of ``mediatools`` (source of the forms).
        program: Program the application menu entry starts.
        environ: Environment to check (replaceable in tests).
    """

    def __init__(self, parser: argparse.ArgumentParser, program: str,
                 environ: dict[str, str] | None = None):
        """Prepare the launcher.

        Args:
            parser: Argument parser of ``mediatools``.
            program: Program the application menu entry starts.
            environ: Environment; ``os.environ`` if None.
        """
        self.parser = parser
        self.program = program
        self.environ = environ if environ is not None else dict(os.environ)

    def problem(self) -> str | None:
        """Return why no window can open.

        Returns:
            Explanation with the remedy, or None if a window can open.
        """
        if not (self.environ.get("DISPLAY") or self.environ.get("WAYLAND_DISPLAY")):
            return ("no graphical session (DISPLAY is not set) - start 'mediatools gui' "
                    "in a terminal of the desktop, not over SSH")
        try:
            importlib.import_module("tkinter")
        except ImportError:
            return ("Tk for Python is not installed - install it once with: "
                    "sudo apt install python3-tk")
        return None

    def run(self, install_desktop: bool = False, remove_desktop: bool = False) -> int:
        """Handle the menu entry options or open the window.

        Args:
            install_desktop: Write the application menu entry and end.
            remove_desktop: Remove the application menu entry and end.

        Returns:
            Process exit code.
        """
        entry = DesktopEntry(self.program)
        if install_desktop:
            print("application menu entry written: " + entry.install())
            return 0
        if remove_desktop:
            print("application menu entry removed" if entry.remove() else "there was no entry")
            return 0
        reason = self.problem()
        if reason:
            print("mediatools gui: " + reason)
            return 2
        tkinter = importlib.import_module("tkinter")
        app_module = importlib.import_module("mediatools.gui.app")
        root = tkinter.Tk(className="mediatools")
        app_module.MediaToolsApp(root, CommandCatalog(self.parser))
        root.mainloop()
        return 0
