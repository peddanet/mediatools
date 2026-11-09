"""`mediatools flatten` - flat hard-link view per unit."""
from __future__ import annotations

import os
from typing import Iterator

from mediatools.core.mediatypes import MediaTypes
from mediatools.core.walker import TreeWalker
from mediatools.flatten.flatindex import FlatIndex
from mediatools.flatten.namer import FlatNamer
from mediatools.flatten.plan import FlatPlan
from mediatools.flatten.planentry import PlanEntry


class Flattener:
    """Creates ``<unit>/_flat/`` with one hard link per media file of the unit.

    Terms:
        root: the directory the command is started in (master level).
        unit: every direct sub-directory of ``root`` (e.g. one project or one source).

    Guarantees:
        * Originals are never modified, moved or deleted.
        * Only ``_flat`` directories are written to.
        * A hard link costs a directory entry, not the file's size.
        * Re-runs only add what is missing (idempotent); a source that is
          already linked under any name is recognised by its inode.

    Workflow: :meth:`analyse` builds a :class:`FlatPlan`; the caller shows
    the summary, saves the plan and calls :meth:`apply` after
    confirmation. :meth:`undo` reverts the links of a saved plan.

    Attributes:
        root: Absolute master directory.
        log: Logger.
        extensions: Extensions that are linked.
        abbreviate: Use folder abbreviations from naming level 1 on.
        prune: Plan removal of links whose source is gone.
        abbreviations: Folder abbreviations (None: namer defaults).
        walker: Tree walker with the shared skip rules.
    """

    def __init__(self, root: str, logger, extensions: frozenset = MediaTypes.MEDIA,
                 abbreviate: bool = True, prune: bool = False,
                 abbreviations: dict[str, str] | None = None):
        """Configure a run.

        Args:
            root: Master directory; its sub-directories are the units.
            logger: :class:`logging.Logger`.
            extensions: Extensions that are linked.
            abbreviate: Use folder abbreviations from naming level 1 on.
            prune: Plan removal of links whose source is gone.
            abbreviations: Folder abbreviations; the namer's defaults if None.
        """
        self.abbreviations = abbreviations
        self.root = os.path.abspath(root)
        self.log = logger
        self.extensions = extensions
        self.abbreviate = abbreviate
        self.prune = prune
        self.walker = TreeWalker()

    # -- discovery -------------------------------------------------------
    def units(self) -> list[str]:
        """List the units below root.

        Returns:
            Absolute paths of direct sub-directories, sorted; hidden
            directories, reserved names and symbolic links are left out.
        """
        out = []
        for entry in sorted(os.scandir(self.root), key=lambda e: e.name):
            if entry.name.startswith(".") or entry.name in MediaTypes.SKIP_DIRS:
                continue
            if entry.is_dir(follow_symlinks=False):
                out.append(entry.path)
        return out

    def sources(self, unit: str) -> Iterator[tuple[str, list[str]]]:
        """List the media files of a unit.

        Args:
            unit: Unit directory.

        Yields:
            ``(absolute_path, path_components_below_unit)``; files on another
            file system are logged and skipped (hard links cannot cross).
        """
        device = os.stat(unit).st_dev
        for path in self.walker.media_files(unit, self.extensions):
            st = os.stat(path, follow_symlinks=False)
            if st.st_dev != device:
                self.log.warning("other file system, skipped: %s", path)
                continue
            yield path, os.path.relpath(path, unit).split(os.sep)

    @staticmethod
    def name_limit(path: str) -> int:
        """Return the maximum file name length of the file system.

        Args:
            path: Any path on the file system.

        Returns:
            Limit in bytes; eCryptfs with encrypted names reports 143.
        """
        return os.statvfs(path).f_namemax

    # -- analysis --------------------------------------------------------
    def analyse(self) -> FlatPlan:
        """Compare every unit with its ``_flat`` directory.

        Returns:
            Plan with one entry per source (and per orphan with ``prune``).
        """
        plan = FlatPlan()
        for unit in self.units():
            self._analyse_unit(unit, plan)
        return plan

    def _analyse_unit(self, unit: str, plan: FlatPlan) -> None:
        """Add the entries of one unit to the plan.

        Args:
            unit: Unit directory.
            plan: Plan that receives the entries.
        """
        flat = os.path.join(unit, MediaTypes.FLAT_DIR)
        namer = FlatNamer(self.name_limit(unit), self.abbreviations if self.abbreviate else {})
        index = FlatIndex(flat).load()
        existing = self._existing(flat)                     # name -> (dev, ino)
        inodes = {key: name for name, key in existing.items()}
        by_source = index.by_source()                      # rel path -> name
        used = set(existing)
        seen_sources: set[str] = set()
        unit_name = os.path.basename(unit)

        for path, parts in self.sources(unit):
            rel = "/".join(parts)
            seen_sources.add(rel)
            st = os.stat(path)
            key = (st.st_dev, st.st_ino)
            if key in inodes:
                # already linked, whatever name it has
                plan.add(PlanEntry("ok", unit_name, os.path.join(flat, inodes[key]), path))
                continue
            result = namer.name(parts)
            if result is None:
                plan.add(PlanEntry("skip", unit_name, "", path,
                                   note="name cannot be shortened"))
                continue
            name, level = result
            old_name = by_source.get(rel)
            if old_name and old_name in existing:
                # same source path, but a new inode: the file was replaced
                plan.add(PlanEntry("relink", unit_name, os.path.join(flat, old_name),
                                   path, level))
                continue
            if name in used:
                name = namer.disambiguate(name, rel)
            used.add(name)
            plan.add(PlanEntry("link", unit_name, os.path.join(flat, name), path, level))

        if self.prune:
            for name, rel in index.entries.items():
                if rel not in seen_sources and name in existing:
                    plan.add(PlanEntry("prune", unit_name, os.path.join(flat, name),
                                       os.path.join(unit, rel), note="source gone"))

    @staticmethod
    def _existing(flat: str) -> dict[str, tuple[int, int]]:
        """Read the current content of a ``_flat`` directory.

        Args:
            flat: Path of the ``_flat`` directory.

        Returns:
            Mapping link name to ``(st_dev, st_ino)``; empty if missing.
        """
        out: dict[str, tuple[int, int]] = {}
        if not os.path.isdir(flat):
            return out
        with os.scandir(flat) as entries:
            for entry in entries:
                if entry.name == FlatIndex.NAME or not entry.is_file(follow_symlinks=False):
                    continue
                st = entry.stat(follow_symlinks=False)
                out[entry.name] = (st.st_dev, st.st_ino)
        return out

    # -- execution -------------------------------------------------------
    def apply(self, plan: FlatPlan) -> dict[str, int]:
        """Execute the ``link``, ``relink`` and ``prune`` entries.

        Args:
            plan: Plan from :meth:`analyse`.

        Returns:
            Counters ``link``, ``relink``, ``prune`` and ``error``.
        """
        done = {"link": 0, "relink": 0, "prune": 0, "error": 0}
        touched: dict[str, FlatIndex] = {}
        for entry in plan.entries:
            if entry.action not in ("link", "relink", "prune"):
                continue
            flat = os.path.dirname(entry.link)
            unit = os.path.dirname(flat)
            index = touched.setdefault(flat, FlatIndex(flat).load())
            try:
                os.makedirs(flat, exist_ok=True)
                if entry.action == "prune":
                    os.remove(entry.link)
                    index.entries.pop(os.path.basename(entry.link), None)
                    done["prune"] += 1
                    continue
                if entry.action == "link":
                    os.link(entry.source, entry.link)
                else:
                    # relink atomically: new link under a temporary name, then rename
                    tmp = entry.link + ".mtlink"
                    os.link(entry.source, tmp)
                    os.replace(tmp, entry.link)
                index.entries[os.path.basename(entry.link)] = os.path.relpath(entry.source, unit)
                done[entry.action] += 1
            except OSError as exc:
                done["error"] += 1
                self.log.error("%s failed for %s: %s", entry.action, entry.source, exc)
        for index in touched.values():
            index.save()
        return done

    def undo(self, plan: FlatPlan) -> int:
        """Remove the links created by a saved plan.

        Only links that still point to the same inode as their source are
        removed, so nothing that changed in between is touched.

        Args:
            plan: Plan loaded with :meth:`FlatPlan.load`.

        Returns:
            Number of removed links.
        """
        removed = 0
        touched: dict[str, FlatIndex] = {}
        for entry in plan.entries:
            if entry.action not in ("link", "relink") or not os.path.exists(entry.link):
                continue
            try:
                if not os.path.samefile(entry.link, entry.source):
                    continue
            except FileNotFoundError:
                continue
            os.remove(entry.link)
            flat = os.path.dirname(entry.link)
            touched.setdefault(flat, FlatIndex(flat).load()).entries.pop(
                os.path.basename(entry.link), None)
            removed += 1
        for index in touched.values():
            index.save()
        return removed
