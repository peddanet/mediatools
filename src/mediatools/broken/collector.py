"""Setting the damaged videos of a shrink root aside."""
from __future__ import annotations

import os

from mediatools.broken.assessment import Assessment
from mediatools.broken.assessor import Assessor
from mediatools.broken.finder import BrokenFinder
from mediatools.broken.quarantine import Quarantine
from mediatools.core.mediatypes import MediaTypes
from mediatools.core.prompt import Prompt


class BrokenCollector:
    """Measures every damaged video, shows the plan and moves the videos.

    Steps: find (:class:`BrokenFinder`), measure each video once
    (:class:`Assessor`, a full trial decode at the lowest CPU priority),
    list the class and the measurement of each video, ask, and move the
    videos with all their names into ``_broken`` (:class:`Quarantine`).
    Videos that are already set aside are left out, and so are videos whose
    output lies in shrink's work folder (an encode runs or waits for
    recovery).

    Attributes:
        quarantine: The ``_broken`` directory of the root.
        finder: Source of damaged videos.
        assessor: Measurement.
        prompt: Prompt for the confirmation.
        log: Logger.
    """

    def __init__(self, quarantine: Quarantine, finder: BrokenFinder, assessor: Assessor,
                 prompt: Prompt | None = None):
        """Wire the collaborators.

        Args:
            quarantine: The ``_broken`` directory of the root.
            finder: Source of damaged videos.
            assessor: Measurement.
            prompt: Prompt; a default :class:`Prompt` if None.
        """
        self.quarantine = quarantine
        self.finder = finder
        self.assessor = assessor
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
            dry_run: Only measure and list.
            ask: Ask before moving.

        Returns:
            0.
        """
        planned = self.plan()
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
            self.log.info("dry run - nothing moved")
            return 0
        if ask and not self.prompt.yes_no(f"Move {len(planned)} video(s) with all their "
                                          "names into the _broken folder?", default=True):
            self.log.info("nothing moved")
            return 0
        for path, reason, assessment in planned:
            if os.path.isfile(path):
                self.quarantine.collect(path, assessment, reason)
        self.log.info("set aside: %i video(s); review them with: mediatools broken --review",
                      len(planned))
        return 0
