"""Looking for an intact copy of a damaged video on the backup disk."""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Callable

from mediatools.core.decodecheck import DecodeCheck
from mediatools.core.probe import MediaProbe


@dataclass
class BackupResult:
    """What the backup holds of one video.

    Attributes:
        state: ``intact`` (a copy decodes completely without errors),
            ``damaged`` (copies exist, none decodes cleanly) or ``missing``
            (no copy under the mirrored path nor anywhere else by name).
        path: The intact copy, or the first damaged one; empty if missing.
        found_by: ``mirror`` (same relative path) or ``search`` (same file
            name elsewhere in the backup); empty if missing.
        note: Why the copies were rejected, or a short remark.
        checked: Local time of the check.
    """

    state: str
    path: str = ""
    found_by: str = ""
    note: str = ""
    checked: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    @property
    def intact(self) -> bool:
        """Tell whether an intact copy exists.

        Returns:
            True for state ``intact``.
        """
        return self.state == "intact"

    def summary(self) -> str:
        """Describe the result in one line.

        Returns:
            Text such as ``intact copy (same path)``.
        """
        where = {"mirror": "same path", "search": "found by name elsewhere"}.get(self.found_by, "")
        if self.state == "intact":
            return f"intact copy in the backup ({where})"
        if self.state == "damaged":
            return f"backup copy damaged as well ({where}): {self.note}"
        return "no copy in the backup"

    def to_dict(self) -> dict:
        """Return the fields for the manifest.

        Returns:
            Plain dictionary.
        """
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> BackupResult | None:
        """Rebuild a result from the manifest.

        Args:
            data: Dictionary written by :meth:`to_dict`, or None.

        Returns:
            The result, or None if there is none.
        """
        if not data:
            return None
        names = ("state", "path", "found_by", "note", "checked")
        return cls(**{key: data[key] for key in names if key in data})


class BackupMirror:
    """The backup disk as a mirror of a source directory (normally the home).

    The backup holds the source directory under the same relative paths:
    ``<source>/a/b.mp4`` is ``<backup>/a/b.mp4``. For every damaged video
    the check runs in two passes:

    1. **Mirror:** the same relative path of each of its names.
    2. **Search:** videos without a copy there are noted and looked up
       afterwards by file name in the *whole* backup tree (one walk for all
       of them), because files may have moved since the backup was made.

    A copy counts only if it decodes completely without a decoder error
    (:meth:`DecodeCheck.verify`, all streams, lowest CPU priority): a
    download that was cut off may have reached the backup in the same
    state. Of several copies the largest intact one wins.

    The backup is only read, never written. Before the check the disk must
    be available (:meth:`unavailable`): the backup directory exists, lies
    on another file system than the collection (an empty mount point
    belongs to the system disk) and holds the mirror of the shrink root (a
    locked encrypted directory shows only scrambled names).

    Attributes:
        backup: Backup directory (mirror of :attr:`source`), or None.
        source: Directory the backup mirrors.
        log: Logger.
        decode: Full decode check.
        probe: Probe for the duration (time limit of the decode).
    """

    TIMEOUT_MIN = 600.0
    TIMEOUT_FACTOR = 2.0

    def __init__(self, backup: str | None, source: str, logger,
                 decode: DecodeCheck | None = None, probe: MediaProbe | None = None):
        """Configure the mirror.

        Args:
            backup: Backup directory, or None if none is configured.
            source: Directory the backup mirrors.
            logger: :class:`logging.Logger`.
            decode: Decode check; a new one if None.
            probe: Probe; a new one if None.
        """
        self.backup = os.path.abspath(backup) if backup else None
        self.source = os.path.abspath(source)
        self.log = logger
        self.decode = decode or DecodeCheck()
        self.probe = probe or MediaProbe()

    def mirror(self, path: str) -> str | None:
        """Return the mirrored path of a file.

        Args:
            path: File below :attr:`source`.

        Returns:
            Its path in the backup, or None if it is not below the source
            or no backup is configured.
        """
        if self.backup is None:
            return None
        rel = os.path.relpath(os.path.abspath(path), self.source)
        if rel == os.curdir or rel.startswith(os.pardir + os.sep) or rel == os.pardir:
            return None
        return os.path.join(self.backup, rel)

    def unavailable(self, root: str) -> str | None:
        """Tell why the backup cannot be checked now.

        Args:
            root: Shrink root (its mirror must exist).

        Returns:
            None if the check can run, otherwise the reason.
        """
        if self.backup is None:
            return ("no backup configured - set it once with: mediatools config --set "
                    "broken.backup=<backup copy of your home>")
        backup: str = self.backup
        mirrored = self.mirror(root)
        checks: list[tuple[Callable[[], bool], str]] = [
            (lambda: not os.path.isdir(backup),
             f"backup disk not mounted ({backup} does not exist)"),
            (lambda: self.device(backup) == self.device(root),
             f"{backup} lies on the same disk as the collection - the backup disk is not "
             "mounted"),
            (lambda: mirrored is None,
             f"the shrink root is not below {self.source}, the directory the backup mirrors"),
            (lambda: not os.path.isdir(str(mirrored)),
             "the mirror of the shrink root is missing on the backup disk (encrypted folder "
             "still locked, or the backup has another layout)")]
        try:
            return next((reason for failed, reason in checks if failed()), None)
        except OSError as err:
            return f"backup not readable: {err}"

    def check(self, names: dict[str, list[str]]) -> dict[str, BackupResult]:
        """Look for intact copies of damaged videos.

        Args:
            names: For each video (key: its first original path) the
                original paths of all its names.

        Returns:
            Result per key. The caller has checked :meth:`unavailable`.
        """
        results: dict[str, BackupResult] = {}
        later: dict[str, list[str]] = {}
        for number, (key, originals) in enumerate(names.items(), 1):
            self.log.info("backup check %i/%i (same path): %s", number, len(names),
                          os.path.basename(key))
            copies = [path for path in (self.mirror(name) for name in originals)
                      if path is not None and os.path.isfile(path)]
            if copies:
                results[key] = self._judge(copies, "mirror")
            if key not in results or not results[key].intact:
                later[key] = originals
        if later:
            self.log.info("searching the whole backup by name for %i video(s) ...", len(later))
            index = self._index({os.path.basename(name) for names_ in later.values()
                                 for name in names_})
            for key, originals in later.items():
                copies = sorted({path for name in originals
                                 for path in index.get(os.path.basename(name), [])})
                if copies:
                    found = self._judge(copies, "search")
                    if found.intact or key not in results:
                        results[key] = found
                elif key not in results:
                    results[key] = BackupResult("missing")
        for key, result in results.items():
            self.log.info("  %s: %s", os.path.basename(key), result.summary())
        return results

    def _index(self, wanted: set[str]) -> dict[str, list[str]]:
        """Walk the whole backup once and collect the files with wanted names.

        Args:
            wanted: File names to look for.

        Returns:
            Mapping file name to paths in the backup.
        """
        index: dict[str, list[str]] = {}
        if self.backup is None:
            return index
        for dirpath, dirs, files in os.walk(self.backup):
            dirs.sort()
            for name in files:
                if name in wanted:
                    index.setdefault(name, []).append(os.path.join(dirpath, name))
        return index

    def _judge(self, copies: list[str], found_by: str) -> BackupResult:
        """Decode the copies, largest first, until one is intact.

        Args:
            copies: Candidate files in the backup.
            found_by: ``mirror`` or ``search``.

        Returns:
            ``intact`` with the first clean copy, else ``damaged`` with the
            reason of the first copy.
        """
        first = ""
        for path in sorted(copies, key=self._size, reverse=True):
            if self._size(path) == 0:
                reason = "empty file"
            else:
                duration = self.probe.duration(path) or 0.0
                limit = max(self.TIMEOUT_MIN, duration * self.TIMEOUT_FACTOR)
                reason = self.decode.verify(path, limit) or ""
            if not reason:
                return BackupResult("intact", path, found_by)
            first = first or reason
        return BackupResult("damaged", sorted(copies, key=self._size, reverse=True)[0],
                            found_by, first)

    @staticmethod
    def device(path: str) -> int:
        """Return the device (file system) of a path.

        Args:
            path: Existing path.

        Returns:
            ``st_dev`` of the path.
        """
        return os.stat(path).st_dev

    @staticmethod
    def _size(path: str) -> int:
        """Return the size of a file, 0 if it cannot be read.

        Args:
            path: File.

        Returns:
            Size in bytes.
        """
        try:
            return os.path.getsize(path)
        except OSError:
            return 0
