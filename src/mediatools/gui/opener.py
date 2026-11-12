"""Opens files and folders with the desktop's default application."""
from __future__ import annotations

import shutil
import subprocess
import threading


class Opener:
    """Hands a path to ``xdg-open`` (file manager, text editor, player).

    Attributes:
        program: The opener program, or None if it is not installed.
    """

    def __init__(self, program: str | None = None):
        """Find the opener.

        Args:
            program: Opener to use; ``xdg-open`` from the search path if None.
        """
        self.program = program or shutil.which("xdg-open")

    def open(self, path: str) -> str:
        """Open a path without waiting for the application.

        Args:
            path: File or folder.

        Returns:
            Message for the GUI.
        """
        if not self.program:
            return "xdg-open is not installed - cannot open " + path
        child = subprocess.Popen([self.program, path],  # pylint: disable=consider-using-with
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
        # collect the exit status in the background (no zombie process)
        threading.Thread(target=child.wait, daemon=True).start()
        return "opened " + path
