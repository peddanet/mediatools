"""Report of files that exist only in the target (nothing is deleted)."""
from __future__ import annotations

import hashlib
import os

from mediatools.core.mediatypes import MediaTypes
from mediatools.core.walker import TreeWalker


class TargetReport:
    """Classifies files that exist in the target but not in the source.

    Each target-only file gets one status:

    ``hardlinked``  another name of the same inode exists in the target;
                    removing this name would free no space
    ``duplicate``   another file in the target has identical content
                    (different inode) - removable without data loss, or
                    could be hard-linked to save space
    ``unique``      the only copy - keep unless you know it is obsolete

    Content comparison: size first, then SHA-256 of the first and last MiB,
    then the full SHA-256 only for files that still match.

    With ``all_duplicates=True`` the whole target is additionally scanned
    for groups of identical files stored in different inodes (space that a
    deduplication by hard links could reclaim).

    Attributes:
        source: Absolute source directory.
        target: Absolute target directory.
        log: Optional logger.
    """

    CHUNK = 1024 * 1024
    WALK_SKIP = (MediaTypes.SHRINK_TMP_DIR,)

    def __init__(self, source: str, target: str, logger=None):
        """Bind the report to source and target.

        Args:
            source: Source directory.
            target: Target directory.
            logger: Optional :class:`logging.Logger`.
        """
        self.source = os.path.abspath(source)
        self.target = os.path.abspath(target)
        self.log = logger
        self._quick: dict[str, str] = {}
        self._full: dict[str, str] = {}

    # -- inventory -------------------------------------------------------
    def _inventory(self, root: str) -> dict[str, os.stat_result]:
        """Stat every regular file of a tree.

        Args:
            root: Directory to scan; ``_flat`` is included on purpose.

        Returns:
            Mapping relative path to stat result.
        """
        walker = TreeWalker()
        walker.skip = set(self.WALK_SKIP)
        return {os.path.relpath(path, root): os.stat(path) for path in walker.files(root)}

    # -- hashing ---------------------------------------------------------
    def quick_hash(self, path: str) -> str:
        """Hash size, first MiB and last MiB of a file (cached).

        Args:
            path: File to hash.

        Returns:
            SHA-256 hex digest.
        """
        if path not in self._quick:
            digest = hashlib.sha256()
            size = os.path.getsize(path)
            digest.update(str(size).encode())
            with open(path, "rb") as handle:
                digest.update(handle.read(self.CHUNK))
                if size > 2 * self.CHUNK:
                    handle.seek(-self.CHUNK, os.SEEK_END)
                    digest.update(handle.read(self.CHUNK))
            self._quick[path] = digest.hexdigest()
        return self._quick[path]

    def full_hash(self, path: str) -> str:
        """Hash the whole file (cached).

        Args:
            path: File to hash.

        Returns:
            SHA-256 hex digest.
        """
        if path not in self._full:
            digest = hashlib.sha256()
            with open(path, "rb") as handle:
                for block in iter(lambda: handle.read(self.CHUNK * 4), b""):
                    digest.update(block)
            self._full[path] = digest.hexdigest()
        return self._full[path]

    def same_content(self, first: str, second: str) -> bool:
        """Tell whether two files are byte-identical.

        Args:
            first: First file.
            second: Second file.

        Returns:
            True if quick hash and full hash are equal.
        """
        return (self.quick_hash(first) == self.quick_hash(second)
                and self.full_hash(first) == self.full_hash(second))

    # -- analysis --------------------------------------------------------
    def analyse(self, all_duplicates: bool = False) -> dict:
        """Classify the target-only files.

        Args:
            all_duplicates: Also collect duplicate groups of the whole target.

        Returns:
            ``{"target_only": [(rel, size, status, same_as), ...],
            "dup_groups": [[rel, ...], ...]}``.
        """
        src = self._inventory(self.source)
        dst = self._inventory(self.target)
        by_size: dict[int, list[str]] = {}
        by_inode: dict[tuple[int, int], list[str]] = {}
        for rel, st in dst.items():
            by_size.setdefault(st.st_size, []).append(rel)
            by_inode.setdefault((st.st_dev, st.st_ino), []).append(rel)

        result = []
        for rel in sorted(set(dst) - set(src)):
            st = dst[rel]
            names = [other for other in by_inode[(st.st_dev, st.st_ino)] if other != rel]
            if names:
                result.append((rel, st.st_size, "hardlinked", names[0]))
                continue
            result.append((rel, st.st_size, *self._duplicate_of(rel, st, dst, by_size)))

        groups = self._dup_groups(dst, by_size) if all_duplicates else []
        return {"target_only": result, "dup_groups": groups}

    def _duplicate_of(self, rel: str, st: os.stat_result, dst: dict,
                      by_size: dict[int, list[str]]) -> tuple[str, str]:
        """Look for another inode with identical content.

        Args:
            rel: Relative path of the target-only file.
            st: Its stat result.
            dst: Target inventory.
            by_size: Target files grouped by size.

        Returns:
            ``("duplicate", other_rel)`` or ``("unique", "")``.
        """
        for candidate in by_size.get(st.st_size, []):
            cst = dst[candidate]
            if candidate == rel or (cst.st_dev, cst.st_ino) == (st.st_dev, st.st_ino):
                continue
            if self.same_content(os.path.join(self.target, rel),
                                 os.path.join(self.target, candidate)):
                return "duplicate", candidate
        return "unique", ""

    def _dup_groups(self, dst: dict, by_size: dict[int, list[str]]) -> list[list[str]]:
        """Find groups of identical files stored in different inodes.

        Args:
            dst: Target inventory.
            by_size: Target files grouped by size.

        Returns:
            Sorted groups of relative paths (one representative per inode).
        """
        groups = []
        for size, rels in by_size.items():
            if size == 0 or len(rels) < 2:
                continue
            reps: dict[tuple[int, int], str] = {}
            for rel in rels:
                st = dst[rel]
                reps.setdefault((st.st_dev, st.st_ino), rel)
            if len(reps) < 2:
                continue
            # cheap quick hash first, expensive full hash only for matches
            quick: dict[str, list[str]] = {}
            for rel in reps.values():
                quick.setdefault(self.quick_hash(os.path.join(self.target, rel)),
                                 []).append(rel)
            for candidates in quick.values():
                if len(candidates) < 2:
                    continue
                full: dict[str, list[str]] = {}
                for rel in candidates:
                    full.setdefault(self.full_hash(os.path.join(self.target, rel)),
                                    []).append(rel)
                groups += [sorted(group) for group in full.values() if len(group) > 1]
        return groups

    # -- output ----------------------------------------------------------
    @staticmethod
    def write(result: dict, path: str) -> dict:
        """Write the TSV report.

        Args:
            result: Output of :meth:`analyse`.
            path: Report file.

        Returns:
            Counters per status, ``bytes_duplicate`` and ``dup_groups``.
        """
        counts = {"unique": 0, "duplicate": 0, "hardlinked": 0, "bytes_duplicate": 0}
        with open(path, "w", encoding="utf-8", errors="surrogateescape") as handle:
            handle.write("# files that exist only in the target (nothing was deleted)\n")
            handle.write("status\tsize\tpath\tsame_as\n")
            for rel, size, status, other in result["target_only"]:
                counts[status] += 1
                if status == "duplicate":
                    counts["bytes_duplicate"] += size
                handle.write(f"{status}\t{size}\t{rel}\t{other}\n")
            if result["dup_groups"]:
                handle.write("\n# identical content stored in different inodes "
                             "(could be hard-linked)\n")
                for number, group in enumerate(result["dup_groups"], 1):
                    for rel in group:
                        handle.write(f"group{number}\t\t{rel}\t\n")
        counts["dup_groups"] = len(result["dup_groups"])
        return counts
