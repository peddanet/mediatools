"""Directory traversal with the project-wide exclusion rules."""
from __future__ import annotations

import os
from typing import Iterable, Iterator

from mediatools.core.mediatypes import MediaTypes


class TreeWalker:
    """Deterministic :func:`os.walk` that never enters reserved directories.

    Skipped are ``_flat``, ``newShrinkedFiles``, hidden directories (``.*``)
    and any additional names given. Directory and file lists are sorted so
    that runs are reproducible.

    Attributes:
        skip: Directory names that are never entered.
    """

    def __init__(self, extra_skip: Iterable[str] = ()):
        """Build the skip set.

        Args:
            extra_skip: Directory names to skip in addition to the defaults.
        """
        self.skip = set(MediaTypes.SKIP_DIRS) | set(extra_skip)

    def walk(self, root: str) -> Iterator[tuple[str, list[str], list[str]]]:
        """Walk a tree top-down.

        Args:
            root: Directory to walk.

        Yields:
            ``(dirpath, dirs, files)`` like :func:`os.walk`, sorted.
        """
        for dirpath, dirs, files in os.walk(root):
            # in-place assignment prunes the walk (documented os.walk feature)
            dirs[:] = sorted(d for d in dirs if d not in self.skip and not d.startswith("."))
            files.sort()
            yield dirpath, dirs, files

    def files(self, root: str) -> Iterator[str]:
        """Walk a tree and return regular files only.

        Args:
            root: Directory to walk.

        Yields:
            Absolute paths of regular files; symbolic links are left out.
        """
        for dirpath, _dirs, names in self.walk(root):
            for name in names:
                path = os.path.join(dirpath, name)
                if os.path.isfile(path) and not os.path.islink(path):
                    yield path

    def media_files(self, root: str, extensions: frozenset = MediaTypes.MEDIA) -> Iterator[str]:
        """Walk a tree and return files with a given extension.

        Args:
            root: Directory to walk.
            extensions: Accepted lower-case extensions including the dot.

        Yields:
            Absolute paths of matching regular files; empty files (0 bytes,
            e.g. triage placeholders) are left out.
        """
        for path in self.files(root):
            if MediaTypes.extension(path) in extensions and os.path.getsize(path) > 0:
                yield path
