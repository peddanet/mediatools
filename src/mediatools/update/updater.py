"""Run the bundled release script for ``mediatools update``."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class ReleaseUpdater:
    """Apply delivered patch archives with the bundled ``apply-release.sh``.

    The shell script is shipped inside the package, so every installed
    release carries it. It finds the patch archives in the download folder,
    asks up to which version to apply, runs the git-flow hotfix steps in the
    clone, installs the result and tidies the download folder (see the
    script header and the developer documentation).

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
        return subprocess.run(self.command(), check=False).returncode
