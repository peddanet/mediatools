"""Persistent list of files the user decided to keep."""
from __future__ import annotations

import os
from typing import Iterable


class KeptList:
    """Remembers kept files across triage sessions.

    Stored as TSV ``path<TAB>size<TAB>mtime`` (mtime as integer Unix time).
    A file counts as kept only while all three values still match, so a
    file that changes (e.g. is shrunk) is offered again.

    Attributes:
        path: Location of the TSV file.
    """

    def __init__(self, path: str):
        """Bind the list to a file.

        Args:
            path: Location of the TSV file (created on first write).
        """
        self.path = path
        self._keys: set[str] | None = None

    @staticmethod
    def key(file_path: str) -> str:
        """Build the identity line of a file.

        Args:
            file_path: File to describe.

        Returns:
            ``path<TAB>size<TAB>mtime``.
        """
        st = os.stat(file_path)
        return f"{file_path}\t{st.st_size}\t{int(st.st_mtime)}"

    def load(self) -> set[str]:
        """Read the list once and cache it.

        Returns:
            Set of identity lines.
        """
        if self._keys is None:
            try:
                with open(self.path, encoding="utf-8") as handle:
                    self._keys = {line.rstrip("\n") for line in handle if line.strip()}
            except FileNotFoundError:
                self._keys = set()
        return self._keys

    def contains(self, file_path: str) -> bool:
        """Tell whether a file is on the list with unchanged size and mtime.

        Args:
            file_path: File to look up.

        Returns:
            True if the identity line is present.
        """
        try:
            return self.key(file_path) in self.load()
        except FileNotFoundError:
            return False

    def add(self, paths: Iterable[str]) -> None:
        """Append files to the list (duplicates are ignored).

        Args:
            paths: Files to remember.
        """
        keys = self.load()
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as handle:
            for path in paths:
                key = self.key(path)
                if key not in keys:
                    handle.write(key + "\n")
                    keys.add(key)
