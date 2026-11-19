"""Setting the damaged videos of a shrink root aside."""
from __future__ import annotations

import os
from datetime import datetime

from mediatools.broken.assessment import Assessment
from mediatools.broken.assessmentstore import AssessmentStore
from mediatools.broken.assessor import Assessor
from mediatools.broken.backup import BackupMirror
from mediatools.broken.finder import BrokenFinder
from mediatools.broken.quarantine import Quarantine, QuarantineItem
from mediatools.broken.quarantineset import QuarantineSet
from mediatools.core.mediatypes import MediaTypes
from mediatools.core.prompt import Prompt


class BrokenCollector:
    """Measures every damaged video, shows the plan and moves the videos.

    Steps: find (:class:`BrokenFinder`), measure each video once
    (:class:`Assessor`, a full trial decode at the lowest CPU priority),
    list the class and the measurement of each video, ask, move the videos
    with all their names into ``_broken`` (:class:`Quarantine`) and check
    the backup for intact copies (:class:`BackupMirror`, read only; also in
    a dry run).

    A video that turns out **not to be damaged** (a false alarm, e.g. only
    irregular time stamps) is not moved; its ``BROKEN`` entry is removed so
    shrink encodes it again. Videos already set aside that were measured by
    an older method are measured again; those not damaged go back.

    Videos already set aside - in the ``_broken`` of the root or of any
    folder below it (:class:`QuarantineSet`) - are not collected twice, and
    videos whose output lies in shrink's work folder (an encode runs or
    waits for recovery) stay in place. Measurements are taken from the
    :class:`AssessmentStore` while the file is unchanged; every result of
    a measurement is reported, damaged or not.

    Attributes:
        quarantine: The ``_broken`` directory of the root (new finds).
        members: All quarantines read (the root's first).
        finder: Source of damaged videos.
        assessor: Measurement.
        backup: Backup mirror, or None to skip the backup check.
        prompt: Prompt for the confirmation.
        store: Stored measurements, or None to measure every time.
        recheck: Check the backup again also where a result exists.
        log: Logger.
    """

    def __init__(self, quarantine: Quarantine, finder: BrokenFinder,
                 assessor: Assessor, backup: BackupMirror | None = None,
                 prompt: Prompt | None = None, **extra):
        """Wire the collaborators.

        Args:
            quarantine: The ``_broken`` directory of the root.
            finder: Source of damaged videos.
            assessor: Measurement.
            backup: Backup mirror, or None to skip the backup check.
            prompt: Prompt; a default :class:`Prompt` if None.
            **extra: ``quarantines`` (:class:`QuarantineSet`, default: only
                ``quarantine``), ``store`` (:class:`AssessmentStore`,
                default: none) and ``recheck`` (bool, default False).
        """
        self.quarantine = quarantine
        quarantines: QuarantineSet | None = extra.get("quarantines")
        self.members: list[Quarantine] = quarantines.members if quarantines else [quarantine]
        self.finder = finder
        self.assessor = assessor
        self.backup = backup
        self.prompt = prompt or Prompt()
        self.store: AssessmentStore | None = extra.get("store")
        self.recheck = bool(extra.get("recheck", False))
        self.log = quarantine.log

    def measure(self, path: str) -> Assessment:
        """Measure a video, or take its stored measurement.

        Args:
            path: Video file.

        Returns:
            The measurement.
        """
        if self.store is None:
            return self.assessor.assess(path)
        return self.store.assess(path, self.assessor.assess)

    def plan(self) -> list[tuple[str, str, Assessment]]:
        """Find and measure the damaged videos.

        Returns:
            ``(path, reason, assessment)`` triples, promising first.
        """
        found = []
        for path, reason in self.finder.find():
            if any(member.holds(path) for member in self.members):
                continue
            if self.in_work(path):
                self.log.warning("left in place, shrink has output of it in its work folder "
                                 "(running or interrupted encode): %s", path)
                continue
            found.append((path, reason))
        planned = []
        for number, (path, reason) in enumerate(found, 1):
            self.log.info("measuring %i/%i: %s", number, len(found),
                          os.path.relpath(path, self.quarantine.root))
            planned.append((path, reason, self.measure(path)))
        planned.sort(key=lambda entry: (entry[2].kind != "promising", entry[0]))
        return planned

    @staticmethod
    def in_work(path: str) -> bool:
        """Tell whether shrink has output of a video in its work folder.

        Such a video is being encoded right now, or its replacement waits
        for the recovery of an interrupted run; it must stay in place.

        Args:
            path: Video file.

        Returns:
            True if ``<dir>/newShrinkedFiles/<name>`` exists.
        """
        return os.path.lexists(os.path.join(os.path.dirname(path), MediaTypes.SHRINK_TMP_DIR,
                                            os.path.basename(path)))

    def run(self, dry_run: bool = False, ask: bool = True) -> int:
        """Set the damaged videos aside.

        Args:
            dry_run: Only measure, check the backup and list.
            ask: Ask before moving.

        Returns:
            0.
        """
        self.remeasure(dry_run)
        planned = self.plan()
        healthy = [entry for entry in planned if entry[2].healthy]
        planned = [entry for entry in planned if not entry[2].healthy]
        self.clear_false_alarms(healthy, dry_run)
        if not planned:
            self.log.info("no new damaged videos found below %s", self.quarantine.root)
            if dry_run:
                self.preview_backup([])
                self.log.info("dry run - nothing moved")
            else:
                self.check_backup()
            return 0
        for path, reason, assessment in planned:
            self.log.info("  %-9s %s  [%s]  %s", assessment.kind, assessment.summary(), reason,
                          os.path.relpath(path, self.quarantine.root))
        counts = {kind: sum(1 for entry in planned if entry[2].kind == kind)
                  for kind in Quarantine.FOLDERS}
        self.log.info("%i promising, %i hopeless -> %s", counts["promising"], counts["hopeless"],
                      self.quarantine.directory)
        if dry_run:
            self.preview_backup([path for path, _reason, _assessment in planned])
            self.log.info("dry run - nothing moved")
            return 0
        if ask and not self.prompt.yes_no(f"Move {len(planned)} video(s) with all their "
                                          "names into the _broken folder?", default=True):
            self.log.info("nothing moved")
            return 0
        for path, reason, assessment in planned:
            if os.path.isfile(path):
                self.quarantine.collect(path, assessment, reason)
        self.log.info("set aside: %i video(s)", len(planned))
        self.check_backup()
        self.log.info("review them with: mediatools broken --review (or the GUI)")
        return 0

    def remeasure(self, dry_run: bool) -> None:
        """Measure set-aside videos again whose measurement used an older method.

        Every result is reported, damaged or not. Those that are not
        damaged go back (to shrink); the others keep their place, moved to
        the folder of their new class if it changed. All quarantines are
        covered.

        Args:
            dry_run: Only measure and report.
        """
        for member in self.members:
            renewed = member.outdated(self.measure)
            for number, (item, assessment) in enumerate(renewed, 1):
                if assessment.healthy:
                    self.log.info("  re-measured %i/%i: not damaged (%s) - %s", number,
                                  len(renewed), assessment.summary(),
                                  "would go back to shrink" if dry_run
                                  else "put back for shrink")
                else:
                    self.log.info("  re-measured %i/%i: damaged, %s (%s)", number, len(renewed),
                                  assessment.kind, assessment.summary())
                if dry_run:
                    continue
                member.reassess(item, assessment)
                if assessment.healthy:
                    member.release(item)

    def clear_false_alarms(self, healthy: list[tuple[str, str, Assessment]],
                           dry_run: bool) -> None:
        """Remove the BROKEN entries of videos that are not damaged.

        Args:
            healthy: ``(path, reason, assessment)`` of videos that decode
                completely without a decoder error.
            dry_run: Only report.
        """
        for path, _reason, assessment in healthy:
            self.log.info("  false alarm, not damaged (%s): %s", assessment.summary(),
                          os.path.relpath(path, self.quarantine.root))
            if dry_run or self.quarantine.flat(path):
                continue
            database = self.quarantine.database(path)
            if database.exists():
                database.clear_failure(path)
        if healthy and not dry_run:
            self.log.info("%i false alarm(s): failure entries removed, shrink encodes them "
                          "again", len(healthy))

    def needs_backup_check(self, item: QuarantineItem) -> bool:
        """Tell whether the backup must be checked for a set-aside video.

        Args:
            item: Manifest entry.

        Returns:
            True if the video is damaged and has no backup result, if
            ``recheck`` is set, or if the backup copy used for the result
            changed after the check.
        """
        if item.assessment.healthy:
            return False
        if item.backup is None or self.recheck:
            return True
        return self.copy_changed(item.backup.path, item.backup.checked)

    @staticmethod
    def copy_changed(path: str, checked: str) -> bool:
        """Tell whether a backup copy was modified after it was checked.

        Args:
            path: The copy (empty if none was found).
            checked: Local time of the check (ISO format).

        Returns:
            True if the copy is newer than the check.
        """
        if not path or not checked:
            return False
        try:
            return int(os.path.getmtime(path)) > datetime.fromisoformat(checked).timestamp()
        except (OSError, ValueError):
            return False

    def check_backup(self) -> None:
        """Check the backup for every set-aside video that needs it (all quarantines)."""
        if self.backup is None:
            return
        for member in self.members:
            items = [item for item in member.open_items() if self.needs_backup_check(item)]
            reason = member.check_backup(self.backup, items) if items else None
            if reason is not None:
                self.log.warning("backup check pending (%s); mount the backup disk, then run: "
                                 "mediatools broken --backup-check", reason)
                return

    def preview_backup(self, paths: list[str]) -> None:
        """Check the backup without storing anything (dry run).

        Covers the damaged videos still in their places and the set-aside
        videos of all quarantines that need a check.

        Args:
            paths: Damaged videos in their places.
        """
        if self.backup is None:
            return
        names: dict[str, list[str]] = {path: self.quarantine.links(path)[0] for path in paths}
        for member in self.members:
            for item in member.open_items():
                if self.needs_backup_check(item):
                    names[item.original] = [pair[0] for pair in item.names]
        if not names:
            return
        reason = self.backup.unavailable(self.quarantine.root)
        if reason is not None:
            self.log.warning("backup not checked (%s)", reason)
            return
        self.log.info("backup preview for %i video(s) - nothing is stored", len(names))
        self.backup.check(names)
