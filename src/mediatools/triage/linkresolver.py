"""Finding the ``_flat`` hard links that belong to a file."""
from __future__ import annotations

import os

from mediatools.core.mediatypes import MediaTypes


class FlatLinkResolver:
    """Finds the names of a file inside the ``_flat`` directories above it.

    ``mediatools flatten`` creates hard links in ``<unit>/_flat/``. Removing
    only the original name would not free any space, because the link keeps
    the content alive. This resolver returns those sibling names so that
    triage can remove them together with the original.

    Only ``_flat`` directories of ancestors up to ``root`` are searched;
    hard links elsewhere (e.g. created by a deduplication tool in another
    unit) are deliberately left alone and reported via the link count.

    Attributes:
        root: Absolute directory above which no ``_flat`` is searched.
    """

    def __init__(self, root: str):
        """Bind the resolver to a search root.

        Args:
            root: Top directory of the triage run.
        """
        self.root = os.path.abspath(root)
        self._index: dict[str, dict[tuple[int, int], list[str]]] = {}

    def _flat_index(self, flat_dir: str) -> dict[tuple[int, int], list[str]]:
        """Return (and cache) the inode index of one ``_flat`` directory.

        Args:
            flat_dir: Path of a ``_flat`` directory.

        Returns:
            Mapping ``(st_dev, st_ino)`` to the paths with that inode.
        """
        if flat_dir not in self._index:
            index: dict[tuple[int, int], list[str]] = {}
            try:
                with os.scandir(flat_dir) as entries:
                    for entry in entries:
                        if entry.is_file(follow_symlinks=False):
                            st = entry.stat(follow_symlinks=False)
                            index.setdefault((st.st_dev, st.st_ino), []).append(entry.path)
            except FileNotFoundError:
                pass
            self._index[flat_dir] = index
        return self._index[flat_dir]

    def flat_names(self, path: str) -> list[str]:
        """List all ``_flat`` names that share the inode of a file.

        Args:
            path: Original file.

        Returns:
            Paths in ``_flat`` directories of the file's ancestors.
        """
        st = os.stat(path, follow_symlinks=False)
        key = (st.st_dev, st.st_ino)
        found: list[str] = []
        directory = os.path.dirname(os.path.abspath(path))
        # climb from the file's directory up to the root
        while True:
            flat = os.path.join(directory, MediaTypes.FLAT_DIR)
            if os.path.isdir(flat):
                found += [p for p in self._flat_index(flat).get(key, []) if p != path]
            if directory == self.root or len(directory) <= len(self.root):
                break
            directory = os.path.dirname(directory)
        return found

    def forget(self, path: str) -> None:
        """Drop a removed name from the cache.

        Args:
            path: Path that no longer exists.
        """
        for index in self._index.values():
            for names in index.values():
                if path in names:
                    names.remove(path)
