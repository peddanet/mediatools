"""Run the bundled release script for ``mediatools update``."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from mediatools import __version__


class ReleaseUpdater:
    """Run ``mediatools update`` with the bundled ``apply-release.sh``.

    The shell script is shipped inside the package, so every installed
    release carries it. Step 1 installs a newer release from the repository
    (for everyone); step 2 applies patch archives from the download folder
    and releases them with ``tools/release.sh`` of the clone (see the script
    header and the developer documentation). The script learns the venv
    and version of the running program from ``MEDIATOOLS_VENV`` and
    ``MEDIATOOLS_VERSION``.

    Example:
        >>> ReleaseUpdater(["--list"]).run()

    Attributes:
        args: Arguments passed on to the script.
        script: Path of the bundled script.
    """

    SCRIPT_NAME = "apply-release.sh"

    def __init__(self, args: list[str]) -> None:
        """Prepare the call.

        Args:
            args: Arguments for the script, e.g. ``["--list"]``.
        """
        self.args = args
        self.script = Path(__file__).resolve().parent / self.SCRIPT_NAME

    def command(self) -> list[str]:
        """Build the command line.

        Returns:
            ``bash <script> <args>``.

        Raises:
            FileNotFoundError: If bash or the bundled script is missing.
        """
        bash = shutil.which("bash")
        if bash is None or not self.script.is_file():
            raise FileNotFoundError(f"bash or {self.script} not found")
        return [bash, str(self.script), *self.args]

    def run(self) -> int:
        """Run the script in this terminal (it may ask questions).

        Returns:
            Exit code of the script.
        """
        env = dict(os.environ, MEDIATOOLS_VENV=sys.prefix, MEDIATOOLS_VERSION=__version__)
        return subprocess.run(self.command(), check=False, env=env).returncode
