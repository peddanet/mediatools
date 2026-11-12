"""Start entry of the GUI in the application menu of the desktop."""
from __future__ import annotations

import os


class DesktopEntry:
    """Writes or removes ``mediatools.desktop`` for the application menu.

    Desktop environments (GNOME, COSMIC, KDE ...) list the applications
    whose ``.desktop`` files lie in ``~/.local/share/applications``
    (freedesktop.org Desktop Entry Specification). The file is written only
    on request (``mediatools gui --install-desktop``); it starts the
    installed program, normally the link ``~/scripts/mediatools``.

    Attributes:
        path: Location of the ``.desktop`` file.
        program: Program started by the entry.
    """

    def __init__(self, program: str, path: str | None = None):
        """Prepare the entry.

        Args:
            program: Absolute path of the ``mediatools`` program.
            path: Location of the file; the user's application folder if None.
        """
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        self.path = path or os.path.join(base, "applications", "mediatools.desktop")
        self.program = program

    def text(self) -> str:
        """Return the content of the file.

        Returns:
            A Desktop Entry of type Application.
        """
        program = self.program.replace("\\", "\\\\").replace('"', '\\"')
        return ("[Desktop Entry]\n"
                "Type=Application\n"
                "Name=mediatools\n"
                "Comment=Shrink, review, flatten and back up media collections\n"
                f'Exec="{program}" gui\n'
                "Icon=applications-multimedia\n"
                "Terminal=false\n"
                "Categories=AudioVideo;Utility;\n")

    def install(self) -> str:
        """Write the file.

        Returns:
            Its location.
        """
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write(self.text())
        return self.path

    def remove(self) -> bool:
        """Remove the file.

        Returns:
            True if a file was removed.
        """
        try:
            os.remove(self.path)
        except FileNotFoundError:
            return False
        return True
