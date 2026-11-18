"""Setting the damaged videos of a shrink root aside."""
from __future__ import annotations

import os

from mediatools.broken.assessment import Assessment
from mediatools.broken.assessor import Assessor
from mediatools.broken.backup import BackupMirror
from mediatools.broken.finder import BrokenFinder
from mediatools.broken.quarantine import Quarantine
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

    Videos already set aside are not collected twice, and videos whose
    output lies in shrink's work folder (an encode runs or waits for
    recovery) stay in place.

    Attributes:
        quarantine: The ``_broken`` directory of the root.
        finder: Source of damaged videos.
        assessor: Measurement.
        backup: Backup mirror, or None to skip the backup check.
        prompt: Prompt for the confirmation.
        log: Logger.
    """

    def __init__(self, quarantine: Quarantine, finder: BrokenFinder,
                 assessor: Assessor, backup: BackupMirror | None = None,
                 prompt: Prompt | None = None):
        """Wire the collaborators.

        Args:
            quarantine: The ``_broken`` directory of the root.
            finder: Source of damaged videos.
            assessor: Measurement.
            backup: Backup mirror, or None to skip the backup check.
            prompt: Prompt; a default :class:`Prompt` if None.
        """
        self.quarantine = quarantine
        self.finder = finder
        self.assessor = assessor
        self.backup = backup
        self.prompt = prompt or Prompt()
        self.log = quarantine.log

    def plan(self) -> list[tuple[str, str, Assessment]]:
        """Find and measure the damaged videos.

        Returns:
            ``(path, reason, assessment)`` triples, promising first.
        """
        found = []
        for path, reason in self.finder.find():
            if self.quarantine.holds(path):
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
            planned.append((path, reason, self.assessor.assess(path)))
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
            self.log.info("no damaged videos found below %s", self.quarantine.root)
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

        Those that are not damaged go back (to shrink); the others keep
        their place, moved to the folder of their new class if it changed.

        Args:
            dry_run: Only measure and report.
        """
        for item, assessment in self.quarantine.outdated(self.assessor.assess):
            if assessment.healthy:
                self.log.info("  not damaged (%s)%s", assessment.summary(),
                              "" if dry_run else " - put back for shrink")
            if dry_run:
                continue
            self.quarantine.reassess(item, assessment)
            if assessment.healthy:
                self.quarantine.release(item)

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

    def check_backup(self) -> None:
        """Check the backup for every set-aside video not checked yet."""
        if self.backup is None:
            return
        items = [item for item in self.quarantine.open_items() if item.backup is None
                 and not item.assessment.healthy]
        reason = self.quarantine.check_backup(self.backup, items) if items else None
        if reason is not None:
            self.log.warning("backup check pending (%s); mount the backup disk, then run: "
                             "mediatools broken --backup-check", reason)

    def preview_backup(self, paths: list[str]) -> None:
        """Check the backup for videos that are not moved yet (dry run).

        Args:
            paths: Damaged videos in their places.
        """
        if self.backup is None or not paths:
            return
        reason = self.backup.unavailable(self.quarantine.root)
        if reason is not None:
            self.log.warning("backup not checked (%s)", reason)
            return
        names = {}
        for path in paths:
            names[path] = self.quarantine.links(path)[0]
        self.backup.check(names)
