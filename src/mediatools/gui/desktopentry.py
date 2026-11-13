"""Start entry of the GUI in the application menu of the desktop."""
from __future__ import annotations

import os
import shutil
import sys


class DesktopEntry:
    """Writes or removes the menu entry and the icon of the GUI.

    Desktop environments (COSMIC, GNOME, KDE ...) list the applications
    whose ``.desktop`` files lie in ``~/.local/share/applications``
    (freedesktop.org Desktop Entry Specification) and take their icons from
    the icon theme folders, here ``~/.local/share/icons/hicolor`` (Icon Theme
    Specification): the scalable SVG and a 256-pixel PNG. ``StartupWMClass``
    matches the window class of the GUI (Tk capitalises the class name
    ``mediatools`` to ``Mediatools``), so the dock shows the running window
    under this entry. The files are written only on request
    (``mediatools gui --install-desktop`` or the GUI menu). Since 1.6.0 the
    request is remembered (``[gui] desktop_entry = yes``); the GUI start and
    ``mediatools update`` then put back a missing or outdated entry
    (:meth:`problems`, :meth:`repair`), because some launchers or clean-up
    tools drop entries and a moved program would leave a dead one.

    Attributes:
        path: Location of the ``.desktop`` file.
        program: Program started by the entry.
        icons: Icon theme folder (``.../icons/hicolor``).
    """

    ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")
    ICONS = (("mediatools.svg", os.path.join("scalable", "apps", "mediatools.svg")),
             ("mediatools-256.png", os.path.join("256x256", "apps", "mediatools.png")))
    WINDOW_CLASS = "Mediatools"

    def __init__(self, program: str, path: str | None = None):
        """Prepare the entry.

        Args:
            program: Absolute path of the ``mediatools`` program.
            path: Location of the file; the user's application folder if None.
        """
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        self.path = path or os.path.join(base, "applications", "mediatools.desktop")
        self.icons = os.path.join(base, "icons", "hicolor")
        self.program = program

    @staticmethod
    def default_program() -> str:
        """Return the program an entry should start.

        Returns:
            ``~/scripts/mediatools`` if it exists, else ``mediatools`` from
            the search path, else this interpreter's script.
        """
        link = os.path.expanduser("~/scripts/mediatools")
        if os.path.exists(link):
            return link
        return shutil.which("mediatools") or os.path.abspath(sys.argv[0])

    def problems(self) -> list[str]:
        """Check entry, icons and program.

        Returns:
            One text per problem; empty if the entry is complete and current.
        """
        found = []
        try:
            with open(self.path, encoding="utf-8") as handle:
                if handle.read() != self.text():
                    found.append("entry outdated: " + self.path)
        except OSError:
            found.append("entry missing: " + self.path)
        for _source, target in self.ICONS:
            if not os.path.isfile(os.path.join(self.icons, target)):
                found.append("icon missing: " + os.path.join(self.icons, target))
        if not os.path.exists(self.program):
            found.append("program missing: " + self.program)
        return found

    def repair(self, wanted: bool) -> list[str]:
        """Put back a missing or outdated entry if one is wanted.

        Args:
            wanted: True if the user asked for the entry (``[gui]
                desktop_entry = yes``).

        Returns:
            The problems that were repaired; empty if nothing was done.
        """
        if not wanted:
            return []
        found = [item for item in self.problems() if not item.startswith("program missing")]
        if found:
            self.install()
        return found

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
                "Icon=mediatools\n"
                f"StartupWMClass={self.WINDOW_CLASS}\n"
                "Terminal=false\n"
                "Categories=AudioVideo;Utility;\n")

    def install(self) -> str:
        """Write the entry and copy the icons.

        Returns:
            Location of the entry.
        """
        for source, target in self.ICONS:
            destination = os.path.join(self.icons, target)
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            shutil.copyfile(os.path.join(self.ICON_DIR, source), destination)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write(self.text())
        return self.path

    def remove(self) -> bool:
        """Remove the entry and the icons.

        Returns:
            True if the entry was removed.
        """
        for _source, target in self.ICONS:
            try:
                os.remove(os.path.join(self.icons, target))
            except FileNotFoundError:
                pass
        try:
            os.remove(self.path)
        except FileNotFoundError:
            return False
        return True
