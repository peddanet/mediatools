"""Overview of all places where mediatools keeps something."""
from __future__ import annotations

import os
import shutil

from mediatools.core.datadir import DataDir
from mediatools.core.privacylists import PrivacyLists


class PlacesReport:
    """Lists configuration, state, data and installation folders.

    Answers "where is ...?" in one place (``mediatools where``), so no
    folder has to be remembered.

    Attributes:
        datadir: Folders of mediatools.
    """

    MODES = {"xdg": "XDG layout (since 1.5.0)",
             "single": "one folder from $MEDIATOOLS_HOME",
             "legacy": "old single folder - move it with: mediatools where --migrate"}

    def __init__(self, datadir: DataDir | None = None):
        """Bind to the folders.

        Args:
            datadir: Folders of mediatools; the default ones if None.
        """
        self.datadir = datadir or DataDir()

    def lines(self) -> list[str]:
        """Build the report.

        Returns:
            Text lines, one place per line.
        """
        data = self.datadir
        venv_base = os.environ.get(DataDir.ENV) or DataDir.LEGACY
        rows = [("configuration", data.config_dir), ("config file", data.config_file),
                ("logs", os.path.join(data.state_dir, "logs")),
                ("runtime state", os.path.join(data.state_dir, "state")),
                ("reports", os.path.join(data.state_dir, "reports")),
                ("flatten plans", os.path.join(data.data_dir, "plans")),
                ("release archives", os.path.join(data.data_dir, "releases")),
                ("installed release", os.path.join(os.path.expanduser(venv_base), "venv")),
                ("program", shutil.which("mediatools") or "(not on PATH)")]
        result = [f"layout: {self.MODES[data.mode]}", ""]
        for label, path in rows:
            mark = "" if os.path.exists(path) else "   (not created yet)"
            result.append(f"{label:<18} {path}{mark}")
        result.append("")
        result += PrivacyLists(data).lines()
        return result
