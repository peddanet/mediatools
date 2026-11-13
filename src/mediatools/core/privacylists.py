"""The two local lists of the privacy scan."""
from __future__ import annotations

import os
import shlex
import subprocess

from mediatools.core.datadir import DataDir


class PrivacyLists:
    """Locates, counts, creates and edits the deny and allow list.

    The lists belong to the configuration (``~/.config/mediatools/privacy``)
    and are never part of the repository: a list of the protected terms
    would itself reveal what it protects. The scan itself is
    ``tools/privacy_scan.py`` in the repository.

    Attributes:
        datadir: Folders of mediatools.
    """

    KINDS = ("deny", "allow")
    HEADER = {"deny": "# privacy scan: forbidden terms, one per line (case is ignored,\n"
                      "# a term never matches inside a longer word, at least 3 characters)\n",
              "allow": "# privacy scan: allowed phrases, one per line; a forbidden term\n"
                       "# inside such a phrase is not reported\n"}

    def __init__(self, datadir: DataDir | None = None):
        """Bind to the folders.

        Args:
            datadir: Folders of mediatools; the default ones if None.
        """
        self.datadir = datadir or DataDir()

    def path(self, kind: str) -> str:
        """Return the file of one list.

        Args:
            kind: ``deny`` or ``allow``.

        Returns:
            Absolute path (the file may not exist).
        """
        return self.datadir.privacy_file(kind)

    def entries(self, kind: str) -> list[str]:
        """Read one list.

        Args:
            kind: ``deny`` or ``allow``.

        Returns:
            Entries without comments and empty lines; empty if the file is missing.
        """
        try:
            with open(self.path(kind), encoding="utf-8") as handle:
                return [entry for entry in (line.split("#", 1)[0].strip() for line in handle)
                        if entry]
        except FileNotFoundError:
            return []

    def ensure(self, kind: str) -> str:
        """Create a list with its explanatory header if it is missing.

        The folder gets mode 700 and the file mode 600: only the owner may
        read the protected terms.

        Args:
            kind: ``deny`` or ``allow``.

        Returns:
            Path of the list.
        """
        path = self.path(kind)
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(self.HEADER[kind])
            os.chmod(path, 0o600)
        return path

    def edit(self, kind: str) -> int:
        """Open one list in ``$EDITOR`` (``nano`` if unset).

        Args:
            kind: ``deny`` or ``allow``.

        Returns:
            Exit code of the editor.
        """
        editor = shlex.split(os.environ.get("EDITOR") or "nano")
        return subprocess.run(editor + [self.ensure(kind)], check=False).returncode

    def lines(self) -> list[str]:
        """Describe both lists.

        Returns:
            One line per list: path and number of entries (or "missing").
        """
        result = []
        for kind in self.KINDS:
            path = self.path(kind)
            state = (f"{len(self.entries(kind))} entries" if os.path.exists(path)
                     else "missing")
            result.append(f"{kind}list   {path}   ({state})")
        return result
