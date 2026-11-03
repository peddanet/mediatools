"""File-system properties relevant for hard-link preserving copies."""
from __future__ import annotations

import os
import tempfile


class FsCheck:
    """Answers "can this directory hold hard links?" and related questions.

    The file-system type is read from ``/proc/self/mounts`` (longest
    matching mount point). The decisive test is practical: a temporary file
    is created and hard-linked in the target directory.

    Attributes:
        mounts_file: Mount table to read.
    """

    HARDLINK_FS = frozenset({"ext2", "ext3", "ext4", "btrfs", "xfs", "f2fs", "zfs",
                             "ecryptfs", "jfs", "reiserfs", "nilfs2", "bcachefs"})
    NO_HARDLINK_FS = frozenset({"vfat", "exfat", "msdos", "fuseblk", "ntfs"})

    def __init__(self, mounts_file: str = "/proc/self/mounts"):
        """Select the mount table.

        Args:
            mounts_file: Mount table to read (replaceable in tests).
        """
        self.mounts_file = mounts_file

    def mounts(self) -> list[tuple[str, str, str]]:
        """Read the mount table.

        Returns:
            List of ``(device, mount point, fs type)``.
        """
        out = []
        with open(self.mounts_file, encoding="utf-8") as handle:
            for line in handle:
                parts = line.split()
                if len(parts) >= 3:
                    # the kernel escapes spaces in mount points as \040
                    out.append((parts[0], parts[1].replace("\\040", " "), parts[2]))
        return out

    def mount_of(self, path: str) -> tuple[str, str, str] | None:
        """Find the mount that contains a path.

        Args:
            path: Any path.

        Returns:
            ``(device, mount point, fs type)`` of the longest matching mount
            point, or None.
        """
        path = os.path.realpath(path)
        matches = [entry for entry in self.mounts()
                   if path == entry[1] or path.startswith(entry[1].rstrip("/") + "/")]
        # the longest mount point is the innermost mount
        return max(matches, key=lambda entry: len(entry[1])) if matches else None

    def fs_type(self, path: str) -> str | None:
        """Return the file-system type of a path.

        Args:
            path: Any path.

        Returns:
            Type such as ``ext4``, or None if unknown.
        """
        entry = self.mount_of(path)
        return entry[2] if entry else None

    @staticmethod
    def can_hardlink(directory: str) -> tuple[bool, str]:
        """Create and hard-link a temporary file in a directory.

        Args:
            directory: Directory to test (must exist).

        Returns:
            ``(ok, explanation)``.
        """
        try:
            handle, src = tempfile.mkstemp(prefix=".mediatools-hl-", dir=directory)
            os.close(handle)
        except OSError as exc:
            return False, f"not writable: {exc}"
        dst = src + ".link"
        try:
            os.link(src, dst)
            ok = os.stat(src).st_nlink == 2
            return ok, "hard links supported" if ok else "link count not increased"
        except OSError as exc:
            return False, f"hard link failed: {exc}"
        finally:
            for path in (dst, src):
                try:
                    os.remove(path)
                except FileNotFoundError:
                    pass

    @staticmethod
    def free_bytes(path: str) -> int:
        """Return the free space available to the user.

        Args:
            path: Any path on the file system.

        Returns:
            Free bytes for unprivileged users.
        """
        st = os.statvfs(path)
        return st.f_bavail * st.f_frsize

    @staticmethod
    def nested(first: str, second: str) -> bool:
        """Tell whether one path lies inside the other (or both are equal).

        Args:
            first: First path.
            second: Second path.

        Returns:
            True if the paths are equal or one contains the other.
        """
        first, second = os.path.realpath(first), os.path.realpath(second)
        return (first == second or first.startswith(second + os.sep)
                or second.startswith(first + os.sep))
