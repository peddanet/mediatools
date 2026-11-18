"""Interactive review of the videos set aside in ``_broken``."""
from __future__ import annotations

import contextlib
import os

from mediatools.broken.downloaderdb import DownloaderDatabase
from mediatools.broken.quarantine import Quarantine, QuarantineItem
from mediatools.broken.repairer import RepairEncoder
from mediatools.core.prompt import Prompt
from mediatools.triage.player import PlayerSession
from mediatools.triage.remover import FileRemover


class BrokenReview:
    """Shows the damaged videos one by one and carries out the decision.

    Order: first the *promising* videos, the most usable first, each on its
    own; then the *hopeless* ones as a group (look at a few samples, delete
    all, or go through them one by one).

    Decisions for one video:

    * ``p`` play the damaged video,
    * ``r`` repair: encode the decodable part into a clean HEVC file, play
      it, then accept it (it replaces the damaged video under all its
      names) or discard it; advised from 50 % usable content on,
    * ``k`` keep: put it back unchanged (shrink then leaves it alone),
    * ``d`` delete: remove it under all its names and, if configured, mark
      it in the downloader's database for a new download,
    * ``s`` skip, ``q`` quit.

    Attributes:
        quarantine: The ``_broken`` directory of the root.
        player: Player session.
        remover: Remover for deleted videos.
        repairer: Encoder for repairs.
        downloader: Downloader database handler.
        prompt: Prompt for the questions.
        log: Logger.
    """

    SAMPLES = 3
    MENU = "[p] play  [r] repair  [k] keep (put back)  [d] delete  [s] skip  [q] quit"

    def __init__(self, quarantine: Quarantine, player: PlayerSession, remover: FileRemover,
                 repairer: RepairEncoder, downloader: DownloaderDatabase,
                 prompt: Prompt | None = None):
        """Wire the collaborators.

        Args:
            quarantine: The ``_broken`` directory of the root.
            player: Player session (or a test double).
            remover: Remover for deleted videos.
            repairer: Encoder for repairs.
            downloader: Downloader database handler.
            prompt: Prompt; a default :class:`Prompt` if None.
        """
        self.quarantine = quarantine
        self.player = player
        self.remover = remover
        self.repairer = repairer
        self.downloader = downloader
        self.prompt = prompt or Prompt()
        self.log = quarantine.log

    def run(self) -> int:
        """Review all open videos.

        Returns:
            0, or 130 after Ctrl+C (players are closed, nothing else changes).
        """
        promising = sorted(self.quarantine.open_items("promising"),
                           key=lambda item: -item.assessment.usable_share)
        hopeless = self.quarantine.open_items("hopeless")
        self.log.info("%i promising and %i hopeless video(s) to review", len(promising),
                      len(hopeless))
        try:
            for number, item in enumerate(promising, 1):
                if not self.review_item(item, f"promising {number}/{len(promising)}"):
                    return 0
            if hopeless:
                self.review_hopeless(hopeless)
        except KeyboardInterrupt:
            self.player.close_all()
            self.log.info("interrupted; decisions made so far are kept")
            return 130
        return 0

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
            advice = "r" if item.assessment.repair_advised else "p"
            answer = self.prompt.text(self.MENU, advice).lower()[:1]
            if answer == "q":
                return False
            if answer == "s":
                break
            if answer == "p":
                self.play([item.held])
            elif answer == "r":
                self.repair(item)
            elif answer == "k":
                self.quarantine.restore(item)
            elif answer == "d":
                self.delete(item)
            else:
                print("Please answer p, r, k, d, s or q.")
        return True

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
        self.log.info("  repair %s", "advised (at least 50 % usable)"
                      if item.assessment.repair_advised else "unlikely to help - look first")

    def play(self, paths: list[str]) -> None:
        """Show videos in the player until the user presses Enter.

        Args:
            paths: Videos to show.
        """
        self.player.open(paths)
        self.prompt.text("Press Enter when you have seen enough")
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
        ok = self.repairer.encode(item.held, out,
                                  lambda fraction: self._progress(min(1.0, fraction / share)))
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
            answer = self.prompt.text("[p] play repaired  [a] accept  [x] discard repair",
                                      "p").lower()[:1]
            if answer == "p":
                self.play([out])
            elif answer == "a":
                if self.quarantine.install(item, out, self.remover):
                    return True
                self._remove_work(out)
                return False
            elif answer == "x":
                self._remove_work(out)
                return False
            else:
                print("Please answer p, a or x.")

    def delete(self, item: QuarantineItem, confirm: bool = True) -> bool:
        """Delete a video under all its names and ask the downloader for it again.

        Args:
            item: Open manifest entry.
            confirm: Ask before deleting.

        Returns:
            True if it was deleted.
        """
        if confirm and not self.prompt.yes_no(
                f"{self.remover.verb.capitalize()} this video ({len(item.names)} name(s))?"):
            return False
        if not self.quarantine.delete(item, self.remover):
            return False
        result = self.downloader.reset(item.original, self.quarantine.root)
        self.log.info("  downloader: %s", result)
        return True

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
            self.log.info("  - %s: %s", os.path.relpath(item.original, self.quarantine.root),
                          item.assessment.summary())
        while True:
            items = [item for item in items if item.state == "open"]
            if not items:
                return True
            answer = self.prompt.text(
                f"[v] view {min(self.SAMPLES, len(items))} sample(s)  [a] delete all  "
                "[o] one by one  [s] skip  [q] quit", "v").lower()[:1]
            if answer in ("s", "q"):
                return answer == "s"
            if answer == "v":
                self.play([item.held for item in items[:self.SAMPLES]])
            elif answer == "a":
                if self.prompt.yes_no(f"{self.remover.verb.capitalize()} all {len(items)} "
                                      "hopeless video(s)?"):
                    for item in items:
                        self.delete(item, confirm=False)
            elif answer == "o":
                for number, item in enumerate(items, 1):
                    if not self.review_item(item, f"hopeless {number}/{len(items)}"):
                        return False

    @staticmethod
    def _progress(fraction: float) -> None:
        """Show the progress of a repair on one terminal line.

        Args:
            fraction: Encoded share 0..1 of the decodable part.
        """
        print(f"\r  repairing ... {100 * fraction:5.1f} %", end="", flush=True)

    @staticmethod
    def _remove_work(path: str) -> None:
        """Remove a repair work file and its ffmpeg message file quietly.

        Args:
            path: Work file.
        """
        for name in (path, path + ".ffmpeg.log"):
            with contextlib.suppress(FileNotFoundError):
                os.remove(name)
