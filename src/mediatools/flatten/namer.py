"""Building flat file names that fit the file system's name limit."""
from __future__ import annotations

import hashlib
import os
import re


class FlatNamer:
    """Encodes the relative path of a file into one file name.

    ``posts/2024/Videos/clip_01.mp4`` becomes
    ``posts__2024__Videos__clip_01.mp4``. The separator ``__`` is used
    because it hardly ever occurs in real folder names.

    If the result exceeds ``limit`` bytes (UTF-8), increasingly compact
    levels are tried:

    ====== ==========================================================
    Level  Rule
    ====== ==========================================================
    0      full path, ``__`` separated
    1      configured folder names abbreviated, ``<id> <date> <time>``
           folders compacted to ``<id>-<YYYYMMDD>T<hhmmss>``
    2      level 1 plus the front of the file stem cut; the tail
           (usually the media id) is kept, ``~`` marks the cut
    3      directory part replaced by an 8-digit hash, file stem cut
    ====== ==========================================================

    The exact origin of every link is always recorded in ``_index.tsv``,
    so even level 3 names stay traceable.

    Attributes:
        limit: Maximum name length in bytes.
        abbrev: Folder name abbreviations used from level 1 on.
    """

    SEP = "__"
    CUT = "~"
    DEFAULT_ABBREV = {"Videos": "V", "Images": "I", "Audios": "U"}
    ID_DATE = re.compile(r"(\d{6,}) (\d{4})-(\d\d)-(\d\d) (\d\d)-(\d\d)-(\d\d)")

    def __init__(self, limit: int = 255, abbrev: dict | None = None):
        """Configure the namer.

        Args:
            limit: Maximum name length in bytes (``statvfs().f_namemax``).
            abbrev: Folder abbreviations; None selects the defaults, an
                empty dict disables abbreviation.
        """
        self.limit = limit
        self.abbrev = self.DEFAULT_ABBREV if abbrev is None else abbrev

    # -- primitives ------------------------------------------------------
    @staticmethod
    def nbytes(name: str) -> int:
        """Return the length of a name as stored by the file system.

        Args:
            name: File name.

        Returns:
            Length in bytes (file-system encoding, normally UTF-8).
        """
        return len(os.fsencode(name))

    def fits(self, name: str) -> bool:
        """Tell whether a name respects the byte limit.

        Args:
            name: File name.

        Returns:
            True if the name is not longer than :attr:`limit` bytes.
        """
        return self.nbytes(name) <= self.limit

    def compact(self, segment: str) -> str:
        """Return the level-1 form of one directory name.

        Args:
            segment: Directory name.

        Returns:
            Abbreviation, compacted id/date form, or the unchanged name.
        """
        match = self.ID_DATE.fullmatch(segment)
        if match:
            groups = match.groups()
            return (f"{groups[0]}-{groups[1]}{groups[2]}{groups[3]}"
                    f"T{groups[4]}{groups[5]}{groups[6]}")
        return self.abbrev.get(segment, segment)

    @staticmethod
    def short_hash(text: str, length: int = 8) -> str:
        """Return a stable short hash of a text.

        Args:
            text: Text to hash.
            length: Number of hex digits.

        Returns:
            Prefix of the SHA-1 hex digest.
        """
        # Naming only, not a security function (bandit B324).
        digest = hashlib.sha1(text.encode("utf-8", "surrogateescape"),
                              usedforsecurity=False)
        return digest.hexdigest()[:length]

    def _cut_stem(self, prefix: str, filename: str) -> str | None:
        """Shorten the file stem from the front until prefix and name fit.

        Args:
            prefix: Directory part including the trailing separator.
            filename: Original file name.

        Returns:
            Shortened name, or None if fewer than 8 bytes of stem would remain.
        """
        stem, ext = os.path.splitext(filename)
        budget = self.limit - self.nbytes(prefix) - self.nbytes(ext) - self.nbytes(self.CUT)
        if budget < 8:
            return None
        tail = stem
        # remove characters (not bytes) so no UTF-8 sequence is broken
        while self.nbytes(tail) > budget:
            tail = tail[1:]
        return f"{prefix}{self.CUT}{tail}{ext}"

    # -- public ----------------------------------------------------------
    def name(self, parts: list[str]) -> tuple[str, int] | None:
        """Build the flat name for a relative path.

        Args:
            parts: Path components below the unit; the last element is the
                file name.

        Returns:
            ``(flat_name, level)``, or None if no level fits the limit.
        """
        dirs, filename = parts[:-1], parts[-1]
        full = self.SEP.join(dirs + [filename])
        if self.fits(full):
            return full, 0
        short_dirs = [self.compact(d) for d in dirs]
        level1 = self.SEP.join(short_dirs + [filename])
        if self.fits(level1):
            return level1, 1
        prefix = self.SEP.join(short_dirs) + self.SEP if short_dirs else ""
        level2 = self._cut_stem(prefix, filename)
        if level2 and self.fits(level2):
            return level2, 2
        prefix = self.short_hash("/".join(dirs)) + self.SEP
        level3 = self._cut_stem(prefix, filename)
        if level3 and self.fits(level3):
            return level3, 3
        return None

    def disambiguate(self, name: str, relpath: str) -> str:
        """Make a name unique by inserting ``~<hash6>`` before the extension.

        Args:
            name: Name that is already taken.
            relpath: Relative source path; makes the hash stable per file.

        Returns:
            New name within the byte limit.
        """
        stem, ext = os.path.splitext(name)
        tag = self.CUT + self.short_hash(relpath, 6)
        while self.nbytes(stem + tag + ext) > self.limit:
            stem = stem[1:]
        return stem + tag + ext
