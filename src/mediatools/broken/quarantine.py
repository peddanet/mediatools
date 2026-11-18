"""The ``_broken`` directory of a shrink root and its manifest."""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from mediatools.broken.assessment import Assessment
from mediatools.broken.backup import BackupMirror, BackupResult
from mediatools.core.mediatypes import MediaTypes
from mediatools.shrink.database import ShrinkDatabase
from mediatools.triage.remover import FileRemover


@dataclass
class QuarantineItem:
    """One damaged video that was set aside, with all its names.

    Attributes:
        number: Running number within the manifest.
        kind: ``promising`` or ``hopeless``.
        names: ``[original, quarantined]`` pairs, one per hard link name;
            the first pair is the name the video was found under.
        assessment: Measurement at the time it was set aside.
        reason: Why it was collected (e.g. the database entry).
        state: ``open``, ``restored`` (kept), ``released`` (not damaged,
            back for shrink), ``recovered`` (replaced by the backup copy),
            ``repaired`` or ``deleted``.
        when: Local time it was set aside.
        outside: Further hard link names outside the shrink root.
        backup: Result of the backup check, or None if not checked yet.
    """

    number: int
    kind: str
    names: list[list[str]]
    assessment: Assessment
    reason: str
    state: str = "open"
    when: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    outside: int = 0
    backup: BackupResult | None = None

    @property
    def original(self) -> str:
        """Return the path the video was found under.

        Returns:
            Original path of the first name.
        """
        return self.names[0][0]

    @property
    def held(self) -> str:
        """Return the path of the video inside the ``_broken`` directory.

        Returns:
            Quarantined path of the first name.
        """
        return self.names[0][1]

    def to_dict(self) -> dict:
        """Return the manifest record.

        Returns:
            Plain dictionary.
        """
        return {"number": self.number, "kind": self.kind, "names": self.names,
                "assessment": self.assessment.to_dict(), "reason": self.reason,
                "state": self.state, "when": self.when, "outside": self.outside,
                "backup": self.backup.to_dict() if self.backup else None}

    @classmethod
    def from_dict(cls, data: dict) -> QuarantineItem:
        """Rebuild an item from a manifest record.

        Args:
            data: Record written by :meth:`to_dict`.

        Returns:
            The item.
        """
        return cls(int(data["number"]), data["kind"], [list(pair) for pair in data["names"]],
                   Assessment.from_dict(data.get("assessment", {})), data.get("reason", ""),
                   data.get("state", "open"), data.get("when", ""), int(data.get("outside", 0)),
                   BackupResult.from_dict(data.get("backup")))


class Quarantine:
    """Moves damaged videos aside and back, keeping their names and origin.

    Every shrink root gets its own ``_broken`` directory with one
    sub-directory per class (:attr:`FOLDERS`). Below it the path relative
    to the root is kept, so the first folder still names the unit a video
    came from and every folder below it is preserved. A video is moved
    with :func:`os.rename` (same file system: no copy, instant). **All
    hard link names of the video inside the root move with it** - also its
    ``_flat`` names - otherwise the damaged data would stay visible under
    another name and no space would be freed later. Names outside the root
    cannot be found; their number is recorded.

    ``_broken/manifest.jsonl`` records every video: class, measurement,
    reason and each ``[original, quarantined]`` name pair. Restoring and
    repairing rely on it; it is written with mode 600 because it contains
    paths of the collection. Every walk of the project skips ``_broken``
    (:attr:`MediaTypes.SKIP_DIRS`), so shrink, flatten and triage leave the
    videos there alone.

    Attributes:
        root: Absolute shrink root.
        log: Logger.
        db_name: File name of the shrink databases.
        directory: The ``_broken`` directory of the root.
        manifest: Path of the manifest.
        items: Loaded manifest entries.
    """

    FOLDERS = {"promising": "aussichtsreich", "hopeless": "hoffnungslos"}
    MANIFEST = "manifest.jsonl"
    KEPT_REASON = "kept after review"

    def __init__(self, root: str, logger, db_name: str = ShrinkDatabase.NAME):
        """Bind the quarantine to a shrink root and read its manifest.

        Args:
            root: Shrink root.
            logger: :class:`logging.Logger`.
            db_name: File name of the shrink databases.
        """
        self.root = os.path.abspath(root)
        self.log = logger
        self.db_name = db_name
        self.directory = os.path.join(self.root, MediaTypes.BROKEN_DIR)
        self.manifest = os.path.join(self.directory, self.MANIFEST)
        self.items: list[QuarantineItem] = self._load()
        self._inodes: dict[tuple[int, int], list[str]] | None = None

    # -- manifest --------------------------------------------------------
    def _load(self) -> list[QuarantineItem]:
        """Read the manifest.

        Returns:
            Its entries; empty if it does not exist.
        """
        if not os.path.isfile(self.manifest):
            return []
        with open(self.manifest, encoding="utf-8", errors="surrogateescape") as handle:
            return [QuarantineItem.from_dict(json.loads(line)) for line in handle if line.strip()]

    def save(self) -> None:
        """Write the manifest completely (mode 600)."""
        os.makedirs(self.directory, exist_ok=True)
        tmp = self.manifest + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", errors="surrogateescape") as handle:
            for item in self.items:
                handle.write(json.dumps(item.to_dict(), ensure_ascii=False) + "\n")
        os.replace(tmp, self.manifest)

    def open_items(self, kind: str | None = None) -> list[QuarantineItem]:
        """Return the videos that are still set aside.

        Args:
            kind: Only this class, or every class if None.

        Returns:
            Items in state ``open``.
        """
        return [item for item in self.items
                if item.state == "open" and (kind is None or item.kind == kind)]

    def holds(self, path: str) -> bool:
        """Tell whether a video is already set aside.

        Args:
            path: Original path.

        Returns:
            True if an open item has this original name.
        """
        path = os.path.abspath(path)
        return any(pair[0] == path for item in self.open_items() for pair in item.names)

    # -- hard links ------------------------------------------------------
    def links(self, path: str) -> tuple[list[str], int]:
        """Find every name of a file inside the root.

        Args:
            path: One name of the file.

        Returns:
            ``(names, outside)``: all names inside the root (``path``
            first) and the number of further names elsewhere.
        """
        st = os.stat(path)
        if st.st_nlink == 1:
            return [path], 0
        names = [path] + [p for p in self._index().get((st.st_dev, st.st_ino), []) if p != path]
        return names, max(0, st.st_nlink - len(names))

    def _index(self) -> dict[tuple[int, int], list[str]]:
        """Return (and cache) the inode index of all multiply linked files.

        ``_flat`` is included; ``_broken``, the shrink work folders and
        hidden directories are not.

        Returns:
            Mapping ``(st_dev, st_ino)`` to paths.
        """
        if self._inodes is None:
            self._inodes = {}
            skip = {MediaTypes.BROKEN_DIR, MediaTypes.SHRINK_TMP_DIR}
            for dirpath, dirs, files in os.walk(self.root):
                dirs[:] = sorted(d for d in dirs if d not in skip and not d.startswith("."))
                for name in sorted(files):
                    path = os.path.join(dirpath, name)
                    st = os.lstat(path)
                    if st.st_nlink > 1 and os.path.isfile(path) and not os.path.islink(path):
                        self._inodes.setdefault((st.st_dev, st.st_ino), []).append(path)
        return self._inodes

    # -- moving ----------------------------------------------------------
    def target(self, original: str, kind: str) -> str:
        """Return a free path for a name inside ``_broken``.

        Args:
            original: Original path below the root.
            kind: ``promising`` or ``hopeless``.

        Returns:
            ``<root>/_broken/<folder>/<relative path>``; with ``.1``, ``.2``
            ... before the extension if that name is taken.
        """
        rel = os.path.relpath(original, self.root)
        base = os.path.join(self.directory, self.FOLDERS[kind], rel)
        stem, ext = os.path.splitext(base)
        candidate, number = base, 0
        while os.path.lexists(candidate):
            number += 1
            candidate = f"{stem}.{number}{ext}"
        return candidate

    def inside(self, path: str) -> bool:
        """Tell whether a path is below the root but not in ``_broken``.

        Args:
            path: Path to check.

        Returns:
            True for a path that may be set aside.
        """
        path = os.path.abspath(path)
        return path.startswith(self.root + os.sep) and \
            not (path + os.sep).startswith(self.directory + os.sep)

    def collect(self, path: str, assessment: Assessment, reason: str) -> QuarantineItem:
        """Move a video with all its names into ``_broken`` and record it.

        Args:
            path: Video below the root.
            assessment: Its measurement (decides the class).
            reason: Why it is set aside.

        Returns:
            The new manifest entry.

        Raises:
            ValueError: If the path is not below the root or already in ``_broken``.
        """
        path = os.path.abspath(path)
        if not self.inside(path):
            raise ValueError(f"not below the shrink root {self.root}: {path}")
        names, outside = self.links(path)
        kind = assessment.kind
        pairs: list[list[str]] = []
        for name in names:
            held = self.target(name, kind)
            os.makedirs(os.path.dirname(held), exist_ok=True)
            os.rename(name, held)
            pairs.append([name, held])
            self.log.debug("set aside: %s -> %s", name, held)
        number = max((item.number for item in self.items), default=0) + 1
        item = QuarantineItem(number, kind, pairs, assessment, reason, outside=outside)
        self.items.append(item)
        self.save()
        if outside:
            self.log.warning("%i further name(s) of this video outside %s stay in place: %s",
                             outside, self.root, path)
        return item

    def restore(self, item: QuarantineItem) -> bool:
        """Move every name of a video back and keep shrink away from it.

        The video is recorded as failed (``BROKEN``, :attr:`KEPT_REASON`) in
        the shrink database of its directory, so shrink does not try it
        again while its size is unchanged.

        Args:
            item: Open manifest entry.

        Returns:
            True if every name is back; False if a name is occupied (that
            name stays in ``_broken``; logged).
        """
        complete = self._move_back(item)
        if complete:
            database = self.database(item.original)
            database.ensure()
            database.record_failure(item.original, "BROKEN", self.KEPT_REASON)
            self._close(item, "restored")
        return complete

    def release(self, item: QuarantineItem) -> bool:
        """Move a video that is not damaged back and hand it to shrink again.

        Unlike :meth:`restore` no failure is recorded; the old ``BROKEN``
        entry is removed, so the next shrink run encodes the video.

        Args:
            item: Open manifest entry.

        Returns:
            True if every name is back.
        """
        if not self._move_back(item):
            return False
        for original, _held in item.names:
            database = self.database(original)
            if database.exists() and not self.flat(original):
                database.clear_failure(original)
        self._close(item, "released")
        return True

    def _move_back(self, item: QuarantineItem) -> bool:
        """Move every name of a video back to its original place.

        Args:
            item: Manifest entry.

        Returns:
            True if every name is back; a taken name stays in ``_broken``.
        """
        complete = True
        for original, held in item.names:
            if os.path.lexists(original):
                self.log.error("cannot move back, the name is taken: %s", original)
                complete = False
                continue
            os.makedirs(os.path.dirname(original), exist_ok=True)
            os.rename(held, original)
        return complete

    def reassess(self, item: QuarantineItem, assessment: Assessment) -> None:
        """Store a new measurement; move the video if its class changed.

        Args:
            item: Open manifest entry.
            assessment: New measurement.
        """
        kind = assessment.kind
        if kind != item.kind:
            for pair in item.names:
                held = self.target(pair[0], kind)
                os.makedirs(os.path.dirname(held), exist_ok=True)
                os.rename(pair[1], held)
                pair[1] = held
            item.kind = kind
        item.assessment = assessment
        self.save()

    def outdated(self, assess: Callable[[str], Assessment]) \
            -> list[tuple[QuarantineItem, Assessment]]:
        """Measure the open videos again whose measurement used an older method.

        Args:
            assess: Measurement of a file (e.g. :meth:`Assessor.assess`).

        Returns:
            ``(item, new measurement)`` pairs; nothing is stored yet.
        """
        old = [item for item in self.open_items() if not item.assessment.current]
        renewed = []
        for number, item in enumerate(old, 1):
            self.log.info("measuring again with the current method %i/%i: %s", number, len(old),
                          os.path.relpath(item.original, self.root))
            renewed.append((item, assess(item.held)))
        return renewed

    def check_backup(self, backup: BackupMirror, items: list[QuarantineItem]) -> str | None:
        """Look for intact copies of videos in the backup and record the results.

        Args:
            backup: Backup mirror.
            items: Manifest entries to check.

        Returns:
            None after the check, or why the backup is not available.
        """
        reason = backup.unavailable(self.root)
        if reason is not None or not items:
            return reason
        results = backup.check({item.original: [pair[0] for pair in item.names]
                                for item in items})
        for item in items:
            self.note_backup(item, results[item.original])
        return None

    def note_backup(self, item: QuarantineItem, result: BackupResult) -> None:
        """Store the result of the backup check of a video.

        Args:
            item: Manifest entry.
            result: Result of :meth:`BackupMirror.check`.
        """
        item.backup = result
        self.save()

    def recover(self, item: QuarantineItem, remover: FileRemover) -> bool:
        """Replace a damaged video by its intact copy from the backup.

        The copy is written next to the first original name (same file
        system), flushed to disk, checked for its size and renamed into
        place; every further name becomes a hard link to it again. Then the
        damaged copies are removed and the failure entries cleared, so the
        next shrink run encodes the recovered original.

        Args:
            item: Open manifest entry with an intact backup copy.
            remover: Remover for the damaged copies.

        Returns:
            True if the copy is in place.
        """
        if item.backup is None or not item.backup.intact:
            self.log.error("no intact backup copy recorded for: %s", item.original)
            return False
        taken = [original for original, _held in item.names if os.path.lexists(original)]
        if taken:
            self.log.error("cannot put the backup copy in place, name taken: %s", taken[0])
            return False
        source = item.backup.path
        os.makedirs(os.path.dirname(item.original), exist_ok=True)
        work = os.path.join(os.path.dirname(item.original),
                            f".{os.path.basename(item.original)}.recover")
        try:
            shutil.copy2(source, work)
            with open(work, "rb") as handle:
                os.fsync(handle.fileno())
            if os.path.getsize(work) != os.path.getsize(source):
                raise OSError("size of the copy differs from the backup")
        except OSError as err:
            self.log.error("copy from the backup failed (%s): %s", err, source)
            if os.path.lexists(work):
                os.remove(work)
            return False
        os.replace(work, item.original)
        for original, _held in item.names[1:]:
            os.makedirs(os.path.dirname(original), exist_ok=True)
            os.link(item.original, original)
        for original, held in item.names:
            remover.remove(held)
            database = self.database(original)
            if database.exists() and not self.flat(original):
                database.clear_failure(original)
        self._close(item, "recovered")
        return True

    def delete(self, item: QuarantineItem, remover: FileRemover) -> bool:
        """Remove every name of a set-aside video and forget its failure entry.

        Args:
            item: Open manifest entry.
            remover: Remover (trash or delete).

        Returns:
            True if every name was removed.
        """
        removed = True
        for original, held in item.names:
            removed = remover.remove(held) and removed
            database = self.database(original)
            if database.exists() and not self.flat(original):
                database.clear_failure(original)
        if removed:
            self._close(item, "deleted")
        return removed

    def install(self, item: QuarantineItem, repaired: str, remover: FileRemover) -> bool:
        """Put a repaired video in place of a damaged one, under every name.

        The repaired file goes to the first original name, every further
        name becomes a hard link to it again; the damaged copies are removed
        and the new file is recorded as shrunk (it is HEVC already) in the
        databases of its directories (``_flat`` has none).

        Args:
            item: Open manifest entry.
            repaired: Validated repaired video (same file system).
            remover: Remover for the damaged copies.

        Returns:
            True if the repaired video is in place.
        """
        taken = [original for original, _held in item.names if os.path.lexists(original)]
        if taken:
            self.log.error("cannot put the repaired video in place, name taken: %s", taken[0])
            return False
        os.makedirs(os.path.dirname(item.original), exist_ok=True)
        os.replace(repaired, item.original)
        for original, _held in item.names[1:]:
            os.makedirs(os.path.dirname(original), exist_ok=True)
            os.link(item.original, original)
        for original, held in item.names:
            remover.remove(held)
            if self.flat(original):
                continue                    # shrink never reads _flat: no database there
            database = self.database(original)
            database.ensure()
            database.clear_failure(original)
            database.record(original)
        self._close(item, "repaired")
        return True

    @staticmethod
    def flat(path: str) -> bool:
        """Tell whether a name lies in a ``_flat`` directory.

        Args:
            path: File path.

        Returns:
            True if its directory is a ``_flat`` directory.
        """
        return os.path.basename(os.path.dirname(path)) == MediaTypes.FLAT_DIR

    def database(self, path: str) -> ShrinkDatabase:
        """Return the shrink database of a video's directory.

        Args:
            path: Video path.

        Returns:
            The database object.
        """
        return ShrinkDatabase(os.path.dirname(path), self.log, self.db_name)

    def _close(self, item: QuarantineItem, state: str) -> None:
        """Set the final state of an entry and save the manifest.

        Args:
            item: Manifest entry.
            state: Final state, e.g. ``restored`` or ``deleted``.
        """
        item.state = state
        self.save()
        self.log.info("%s: %s", state.upper(), item.original)
