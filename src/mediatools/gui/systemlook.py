"""Light or dark scheme and accent colour preferred by the desktop."""
from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable


class SystemLook:
    """Reads the appearance settings of the desktop from the settings portal.

    Desktops publish the user's choice through the XDG desktop portal
    (``org.freedesktop.portal.Settings`` on the session bus), namespace
    ``org.freedesktop.appearance``:

    * ``color-scheme`` (``u``): 0 no preference, 1 prefer dark, 2 prefer
      light; unknown values count as 0;
    * ``accent-color`` (``(ddd)``): RGB in the range 0..1; values out of
      range count as unset.

    The values are read with ``gdbus`` (package ``libglib2.0-bin``, present
    on GNOME-based systems such as Pop!_OS), so no Python D-Bus binding is
    needed: first with ``ReadOne`` (interface version 2), then with the
    older ``Read``, whose answer is wrapped in one more variant. Without
    ``gdbus``, without a portal or without the key every value is None and
    the caller uses its own default.

    Attributes:
        run: Runs a command and returns its result (replaceable in tests).
        gdbus: Path of ``gdbus``, or None.
    """

    DEST = "org.freedesktop.portal.Desktop"
    PATH = "/org/freedesktop/portal/desktop"
    INTERFACE = "org.freedesktop.portal.Settings"
    NAMESPACE = "org.freedesktop.appearance"
    TIMEOUT = 2.0
    NUMBER = re.compile(r"-?\d+(?:\.\d+)?(?:[eE]-?\d+)?")

    def __init__(self, run: Callable[..., subprocess.CompletedProcess] | None = None,
                 gdbus: str | None = None):
        """Find ``gdbus``.

        Args:
            run: Command runner; :func:`subprocess.run` if None.
            gdbus: Path of ``gdbus``; looked up in ``PATH`` if None.
        """
        self.run = run or subprocess.run
        self.gdbus = gdbus or shutil.which("gdbus")

    def read(self, key: str) -> str | None:
        """Read one key of the appearance namespace.

        Args:
            key: ``color-scheme`` or ``accent-color``.

        Returns:
            The answer of ``gdbus`` (GVariant text), or None if unavailable.
        """
        if self.gdbus is None:
            return None
        for method in ("ReadOne", "Read"):
            try:
                result = self.run([self.gdbus, "call", "--session", "--dest", self.DEST,
                                   "--object-path", self.PATH, "--method",
                                   f"{self.INTERFACE}.{method}", self.NAMESPACE, key],
                                  capture_output=True, text=True, timeout=self.TIMEOUT,
                                  check=False)
            except (OSError, subprocess.TimeoutExpired):
                return None
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        return None

    def scheme(self) -> str | None:
        """Return the preferred colour scheme.

        Returns:
            ``dark``, ``light``, or None for no preference or no portal.
        """
        answer = self.read("color-scheme")
        match = re.search(r"uint32 (\d+)", answer or "")
        if not match:
            return None
        return {1: "dark", 2: "light"}.get(int(match.group(1)))

    def accent(self) -> str | None:
        """Return the preferred accent colour.

        Returns:
            Colour as ``#rrggbb``, or None if unset or unavailable.
        """
        answer = self.read("accent-color")
        if not answer:
            return None
        values = [float(text) for text in self.NUMBER.findall(answer)]
        if len(values) != 3 or any(value < 0 or value > 1 for value in values):
            return None
        return "#" + "".join(f"{round(value * 255):02x}" for value in values)
