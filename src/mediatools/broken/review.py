"""Interactive review of the videos set aside in ``_broken``."""
from __future__ import annotations

import contextlib
import os

from mediatools.broken.assessor import Assessor
from mediatools.broken.backup import BackupMirror
from mediatools.broken.downloaderdb import DownloaderDatabase
from mediatools.broken.quarantine import Quarantine, QuarantineItem
from mediatools.broken.repairer import RepairEncoder
from mediatools.core.prompt import Prompt
from mediatools.triage.player import PlayerSession
from mediatools.triage.remover import FileRemover


class BrokenReview:  # pylint: disable=too-many-instance-attributes
    """Shows the damaged videos one by one and carries out the decision.

    Order: first the *promising* videos, the most usable first, each on its
    own; then the *hopeless* ones as a group (look at a few samples, act on
    all, or go through them one by one). A video measured by an older
    method is measured again first; one that turns out not to be damaged is
    offered to be put back for shrink.

    **Backup first.** Nothing is repaired or deleted before the backup was
    checked (:class:`BackupMirror`): repairing and deleting are offered
    only once the check found no intact copy. With an intact copy the
    video is recovered from the backup instead. If the backup disk is not
    mounted, the review asks for it and checks again on request.

    Decisions for one video:

    * ``p`` play the damaged video,
    * ``c`` check the backup (offered until checked),
    * ``b`` recover from the backup (only with an intact copy),
    * ``r`` repair: encode the decodable part into a clean HEVC file, play
      it, then accept or discard it (only without an intact copy),
    * ``k`` keep: put it back unchanged; a video that is not damaged goes
      back to shrink, a damaged one is left alone by shrink,
    * ``d`` delete under all names and, if configured, mark it in the
      downloader's database for a new download (only without an intact
      copy),
    * ``s`` skip, ``q`` quit.

    Attributes:
        quarantine: The ``_broken`` directory of the root.
        player: Player session.
        remover: Remover for deleted videos.
        repairer: Encoder for repairs.
        downloader: Downloader database handler.
        backup: Backup mirror.
        assessor: Measurement for videos measured by an older method.
        prompt: Prompt for the questions.
        log: Logger.
    """

    SAMPLES = 3
    LABELS = {"p": "play", "c": "check backup", "b": "recover from backup", "r": "repair",
              "k": "keep (put back)", "d": "delete", "s": "skip", "q": "quit"}

    def __init__(self, quarantine: Quarantine, player: PlayerSession,
                 remover: FileRemover, repairer: RepairEncoder,
                 downloader: DownloaderDatabase, backup: BackupMirror,
                 assessor: Assessor | None = None, prompt: Prompt | None = None):
        # pylint: disable=too-many-arguments,too-many-positional-arguments
        """Wire the collaborators.

        Args:
            quarantine: The ``_broken`` directory of the root.
            player: Player session (or a test double).
            remover: Remover for deleted videos.
            repairer: Encoder for repairs.
            downloader: Downloader database handler.
            backup: Backup mirror.
            assessor: Measurement; a new one if None.
            prompt: Prompt; a default :class:`Prompt` if None.
        """
        self.quarantine = quarantine
        self.player = player
        self.remover = remover
        self.repairer = repairer
        self.downloader = downloader
        self.backup = backup
        self.assessor = assessor or Assessor()
        self.prompt = prompt or Prompt()
        self.log = quarantine.log
        self.stopped = False

    def run(self) -> int:
        """Review all open videos.

        Returns:
            0, or 130 after Ctrl+C (players are closed, nothing else changes).
        """
        try:
            self.remeasure()
            promising = sorted(self.quarantine.open_items("promising"),
                               key=lambda item: -item.assessment.usable_share)
            hopeless = self.quarantine.open_items("hopeless")
            self.log.info("%i promising and %i hopeless video(s) to review", len(promising),
                          len(hopeless))
            for number, item in enumerate(promising, 1):
                if not self.review_item(item, f"promising {number}/{len(promising)}"):
                    self.stopped = True
                    return 0
            if hopeless:
                self.review_hopeless(hopeless)
        except KeyboardInterrupt:
            self.player.close_all()
            self.log.info("interrupted; decisions made so far are kept")
            return 130
        return 0

    def remeasure(self) -> None:
        """Measure the videos again whose measurement used an older method."""
        for item, assessment in self.quarantine.outdated(self.assessor.assess):
            self.quarantine.reassess(item, assessment)

    # -- one video -------------------------------------------------------
    def choices(self, item: QuarantineItem) -> tuple[list[str], str]:
        """Return the decisions open for a video and the advised one.

        Args:
            item: Open manifest entry.

        Returns:
            ``(answers, default)``.
        """
        result = item.backup
        if item.assessment.healthy:
            return ["p", "k", "s", "q"], "k"
        if result is not None and result.intact:
            return ["p", "b", "k", "s", "q"], "b"
        if result is None:
            return ["p", "c", "k", "s", "q"], "c"
        return ["p", "r", "k", "d", "s", "q"], "r" if item.assessment.repair_advised else "p"

    def review_item(self, item: QuarantineItem, title: str) -> bool:
        """Ask for the decision on one video and carry it out.

        Args:
            item: Open manifest entry.
            title: Position shown in the heading.

        Returns:
            False if the user quits, otherwise True.
        """
        self._show(item, title)
        while item.state == "open":
            keys, default = self.choices(item)
            answer = self.prompt.choose(f"{title}: what to do?",
                                        [(key, self.LABELS[key]) for key in keys], default, "q")
            if answer == "q":
                return False
            if answer == "s":
                break
            self._act(item, answer)
        return True

    def _act(self, item: QuarantineItem, answer: str) -> None:
        """Carry out one decision.

        Args:
            item: Open manifest entry.
            answer: One of the keys of :attr:`LABELS` except s and q.
        """
        if answer == "p":
            self.play([item.held])
        elif answer == "c":
            self.check_backup([item])
        elif answer == "b":
            self.quarantine.recover(item, self.remover)
        elif answer == "r":
            self.repair(item)
        elif answer == "k":
            self.keep(item)
        elif answer == "d":
            self.delete(item)

    def _show(self, item: QuarantineItem, title: str) -> None:
        """Log the facts the decision rests on.

        Args:
            item: Manifest entry.
            title: Heading.
        """
        self.log.info("=" * 70)
        self.log.info("%s: %s", title, os.path.relpath(item.original, self.quarantine.root))
        self.log.info("  %s", item.assessment.summary())
        if item.assessment.note:
            self.log.info("  first decoder message: %s", item.assessment.note)
        if len(item.names) > 1:
            self.log.info("  %i names (hard links) are handled together", len(item.names))
        if item.assessment.healthy:
            self.log.info("  not damaged: decodes completely without a decoder error - "
                          "keep puts it back for shrink")
            return
        self.log.info("  backup: %s", item.backup.summary() if item.backup
                      else "not checked yet (repair and delete only after the check)")
        self.log.info("  repair %s", "advised (at least 50 % usable)"
                      if item.assessment.repair_advised else "unlikely to help - look first")

    def keep(self, item: QuarantineItem) -> bool:
        """Put a video back: to shrink if it is not damaged, else left alone.

        Args:
            item: Open manifest entry.

        Returns:
            True if every name is back.
        """
        if item.assessment.healthy:
            return self.quarantine.release(item)
        return self.quarantine.restore(item)

    def check_backup(self, items: list[QuarantineItem]) -> bool:
        """Look for intact copies of videos in the backup and record the results.

        Args:
            items: Open manifest entries.

        Returns:
            False if the backup is not available (the reason is logged).
        """
        reason = self.quarantine.check_backup(self.backup, items)
        if reason is not None:
            self.log.warning("backup check not possible: %s", reason)
            self.log.warning("mount the backup disk (and unlock it), then check again")
            return False
        return True

    def play(self, paths: list[str]) -> None:
        """Show videos in the player until the user confirms.

        Args:
            paths: Videos to show.
        """
        self.player.open(paths)
        self.prompt.pause("Close the question when you have seen enough")
        self.player.close_all()

    def repair(self, item: QuarantineItem) -> bool:
        """Repair a video, show the result and install it if accepted.

        Args:
            item: Open manifest entry.

        Returns:
            True if the repaired video replaced the damaged one.
        """
        out = self.repairer.output_for(item.held)
        self.log.info("repairing (HEVC, lowest CPU priority) ...")
        share = max(0.01, item.assessment.decoded_share)
        shown = [-1]
        ok = self.repairer.encode(item.held, out, lambda fraction: self._progress(
            min(1.0, fraction / share), shown))
        print()
        if not ok:
            self.log.error("repair failed, the video stays in %s", self.quarantine.directory)
            self._remove_work(out)
            return False
        length = self.repairer.probe.duration(out) or 0.0
        ratio = length / item.assessment.duration if item.assessment.duration else 0.0
        self.log.info("repaired video: %.0f s (%.0f %% of the original length)", length,
                      100 * ratio)
        while True:
            answer = self.prompt.choose("Repaired video", [("p", "play repaired"),
                                                           ("a", "accept"),
                                                           ("x", "discard repair")], "p", "x")
            if answer == "p":
                self.play([out])
            elif answer == "a":
                if self.quarantine.install(item, out, self.remover):
                    return True
                self._remove_work(out)
                return False
            else:
                self._remove_work(out)
                return False

    def delete(self, item: QuarantineItem, confirm: bool = True) -> bool:
        """Delete a video under all its names and ask the downloader for it again.

        Refused unless the backup was checked and holds no intact copy.

        Args:
            item: Open manifest entry.
            confirm: Ask before deleting.

        Returns:
            True if it was deleted.
        """
        if item.backup is None or item.backup.intact:
            self.log.error("not deleted: %s", "the backup was not checked yet"
                           if item.backup is None else "the backup holds an intact copy")
            return False
        if confirm and not self.prompt.yes_no(
                f"{self.remover.verb.capitalize()} this video ({len(item.names)} name(s))?"):
            return False
        if not self.quarantine.delete(item, self.remover):
            return False
        result = self.downloader.reset(item.original, self.quarantine.root)
        self.log.info("  downloader: %s", result)
        return True

    # -- the hopeless group ----------------------------------------------
    def review_hopeless(self, items: list[QuarantineItem]) -> bool:
        """Handle the hopeless videos as a group.

        Args:
            items: Open hopeless entries.

        Returns:
            False if the user quits, otherwise True.
        """
        self.log.info("=" * 70)
        self.log.info("%i hopeless video(s):", len(items))
        for item in items:
            self.log.info("  - %s: %s; backup: %s",
                          os.path.relpath(item.original, self.quarantine.root),
                          item.assessment.summary(),
                          item.backup.summary() if item.backup else "not checked yet")
        while True:
            items = [item for item in items if item.state == "open"]
            if not items:
                return True
            unchecked = [item for item in items if item.backup is None]
            intact = [item for item in items if item.backup and item.backup.intact]
            lost = [item for item in items if item.backup and not item.backup.intact]
            choices = [("v", f"view {min(self.SAMPLES, len(items))} sample(s)")]
            if unchecked:
                choices.append(("c", f"check backup ({len(unchecked)})"))
            if intact:
                choices.append(("b", f"recover all from backup ({len(intact)})"))
            if lost:
                choices.append(("a", f"delete all without a backup copy ({len(lost)})"))
            choices += [("o", "one by one"), ("s", "skip"), ("q", "quit")]
            answer = self.prompt.choose("Hopeless videos", choices,
                                        "c" if unchecked else "v", "q")
            if answer in ("s", "q"):
                return answer == "s"
            if not self._group_action(answer, items, unchecked, intact, lost):
                return False

    def _group_action(self, answer: str, items: list[QuarantineItem],
                      unchecked: list[QuarantineItem], intact: list[QuarantineItem],
                      lost: list[QuarantineItem]) -> bool:
        # pylint: disable=too-many-arguments,too-many-positional-arguments
        """Carry out one decision on the hopeless group.

        Args:
            answer: v, c, b, a or o.
            items: Open hopeless entries.
            unchecked: Those not checked against the backup.
            intact: Those with an intact backup copy.
            lost: Those checked without an intact copy.

        Returns:
            False if the user quit while going one by one.
        """
        if answer == "v":
            self.play([item.held for item in items[:self.SAMPLES]])
        elif answer == "c":
            self.check_backup(unchecked)
        elif answer == "b":
            for item in intact:
                self.quarantine.recover(item, self.remover)
        elif answer == "a":
            if self.prompt.yes_no(f"{self.remover.verb.capitalize()} {len(lost)} hopeless "
                                  "video(s) without a backup copy?"):
                for item in lost:
                    self.delete(item, confirm=False)
        elif answer == "o":
            for number, item in enumerate(items, 1):
                if not self.review_item(item, f"hopeless {number}/{len(items)}"):
                    return False
        return True

    @staticmethod
    def _progress(fraction: float, shown: list[int]) -> None:
        """Show the progress of a repair in steps of 5 %.

        Args:
            fraction: Encoded share 0..1 of the decodable part.
            shown: Last shown step (updated).
        """
        step = int(fraction * 20)
        if step != shown[0]:
            shown[0] = step
            print(f"\r  repairing ... {5 * step:3d} %", end="", flush=True)

    @staticmethod
    def _remove_work(path: str) -> None:
        """Remove a repair work file and its ffmpeg message file quietly.

        Args:
            path: Work file.
        """
        for name in (path, path + ".ffmpeg.log"):
            with contextlib.suppress(FileNotFoundError):
                os.remove(name)
