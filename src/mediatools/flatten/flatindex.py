"""The ``_flat/_index.tsv`` mapping file."""
from __future__ import annotations

import os


class FlatIndex:
    """Maps flat link names to their source path relative to the unit.

    File format (UTF-8, tab separated, one line per link)::

        <link name><TAB><relative source path>

    The index makes every link traceable regardless of how strongly its
    name had to be shortened. Independently of the index, the original of a
    link can always be found by inode: ``find <unit> -samefile <link>``.

    Attributes:
        path: Location of ``_index.tsv``.
        entries: Mapping link name to relative source path.
    """

    NAME = "_index.tsv"

    def __init__(self, flat_dir: str):
        """Bind the index to a ``_flat`` directory.

        Args:
            flat_dir: Path of the ``_flat`` directory.
        """
        self.path = os.path.join(flat_dir, self.NAME)
        self.entries: dict[str, str] = {}

    def load(self) -> "FlatIndex":
        """Read the index if it exists.

        Returns:
            The object itself, to allow ``FlatIndex(d).load()``.
        """
        self.entries = {}
        try:
            with open(self.path, encoding="utf-8", errors="surrogateescape") as handle:
                for line in handle:
                    line = line.rstrip("\n")
                    if "\t" in line:
                        name, rel = line.split("\t", 1)
                        self.entries[name] = rel
        except FileNotFoundError:
            pass
        return self

    def save(self) -> None:
        """Write the index atomically (temporary file + rename)."""
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8", errors="surrogateescape") as handle:
            for name in sorted(self.entries):
                handle.write(f"{name}\t{self.entries[name]}\n")
        os.replace(tmp, self.path)

    def by_source(self) -> dict[str, str]:
        """Return the inverse mapping.

        Returns:
            Mapping relative source path to link name.
        """
        return {rel: name for name, rel in self.entries.items()}
