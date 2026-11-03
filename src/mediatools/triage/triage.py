"""`mediatools triage` - review short videos in batches."""
from __future__ import annotations

import os
import signal
import sys
from typing import Iterable

from mediatools.core.datadir import DataDir
from mediatools.core.prompt import Prompt
from mediatools.triage.keptlist import KeptList
from mediatools.triage.linkresolver import FlatLinkResolver
from mediatools.triage.player import PlayerSession
from mediatools.triage.remover import FileRemover


class Triage:
    """Interactive batch review: close a window to discard that video.

    Workflow per batch:

    1. open ``batch`` candidates, one player process each
    2. the user closes the windows of unwanted videos and presses Enter
    3. summary (remove / keep / player error) and confirmation
    4. removal of each discarded file **together with its ``_flat`` hard
       links** (otherwise no space would be freed); kept files go to the
       kept list; remaining players are closed

    Attributes:
        root: Absolute start directory.
        log: Logger.
        finder: Iterable of ``(path, duration)`` candidates.
        player: Player session.
        remover: File remover.
        kept: Kept list.
        batch: Videos per batch.
        prompt: Prompt for the questions.
        links: Resolver for ``_flat`` names.
        batch_no: Number of the current batch.
    """

    def __init__(self, root: str, logger, finder: Iterable[tuple[str, float]],
                 player: PlayerSession, remover: FileRemover, kept: KeptList,
                 batch: int = 10, prompt: Prompt | None = None):
        """Wire the collaborators.

        Args:
            root: Start directory of the run.
            logger: :class:`logging.Logger`.
            finder: Candidates, usually a :class:`CandidateFinder`.
            player: Player session (or a test double).
            remover: File remover.
            kept: Kept list.
            batch: Videos per batch.
            prompt: Prompt; a default :class:`Prompt` if None.
        """
        self.root = os.path.abspath(root)
        self.log = logger
        self.finder = finder
        self.player = player
        self.remover = remover
        self.kept = kept
        self.batch = batch
        self.prompt = prompt or Prompt()
        self.links = FlatLinkResolver(self.root)
        self.batch_no = 0

    def install_signal_handlers(self) -> None:
        """Ctrl+C / SIGTERM close all players and remove nothing."""
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, self._on_signal)

    def _on_signal(self, signum, _frame) -> None:
        """Close players and exit with code 130.

        Args:
            signum: Number of the received signal.
            _frame: Current stack frame (unused).
        """
        self.player.close_all()
        self.log.info("interrupted by %s; nothing removed in this batch",
                      signal.Signals(signum).name)
        sys.exit(130)

    def run(self) -> int:
        """Review all candidates batch by batch.

        Returns:
            Always 0 (quitting is a normal end).
        """
        batch: list[tuple[str, float]] = []
        for item in self.finder:
            batch.append(item)
            if len(batch) == self.batch:
                if not self.review(batch):
                    self.log.info("session ended by user")
                    return 0
                batch = []
        if batch:
            self.review(batch)
        self.log.info("no more matching videos - session complete")
        return 0

    def review(self, batch: list[tuple[str, float]]) -> bool:
        """Handle one batch.

        Args:
            batch: ``(path, duration)`` pairs of this batch.

        Returns:
            False if the user quits the session, otherwise True.
        """
        self.batch_no += 1
        self.log.info("=" * 70)
        self.log.info("batch %i: %i videos", self.batch_no, len(batch))
        for number, (path, duration) in enumerate(batch, 1):
            self.log.info("  [%2d] %6.1fs  %s", number, duration, path)
        self.player.open([path for path, _ in batch])
        print("\nClose every player window whose video you want to REMOVE.")
        answer = self.prompt.text("Then press Enter here (s = skip batch, q = quit)").lower()
        if answer in ("s", "q"):
            self.player.close_all()
            self.log.info("batch %i skipped, nothing changed", self.batch_no)
            return answer != "q"

        closed, still_open, errors = self.player.classify()
        plan = {path: self.links.flat_names(path) for path in closed}
        self._summary(plan, still_open, errors)
        confirmed = bool(closed) and self.prompt.yes_no(
            f"{self.remover.verb} {len(closed)} file(s) and their _flat links?")
        self.player.close_all()

        if confirmed:
            for path, flat_names in plan.items():
                for name in [path] + flat_names:
                    if self.remover.remove(name):
                        self.links.forget(name)
            self.kept.add(still_open)
        elif closed:
            self.log.info("not confirmed, nothing removed")
        else:
            self.kept.add(still_open)
        return True

    def _summary(self, plan: dict[str, list[str]], still_open: list[str],
                 errors: list[tuple[str, int]]) -> None:
        """Log what will happen with each file of the batch.

        Args:
            plan: Closed files mapped to their ``_flat`` names.
            still_open: Files whose player is still running.
            errors: ``(path, exit_code)`` of players that failed.
        """
        self.log.info("closed -> %s: %i", self.remover.verb, len(plan))
        for path, flat in plan.items():
            extra = self._other_links(path, flat)
            self.log.info("    - %s%s%s", path,
                          f"  (+{len(flat)} _flat link)" if flat else "",
                          f"  [!{extra} further hard link(s) elsewhere: space not freed]"
                          if extra else "")
        self.log.info("still open -> keep: %i", len(still_open))
        for path in still_open:
            self.log.info("    + %s", path)
        if errors:
            self.log.info("player error / ended at once -> keep: %i", len(errors))
            for path, code in errors:
                self.log.info("    ! (rc=%s) %s", code, path)

    @staticmethod
    def _other_links(path: str, flat: list[str]) -> int:
        """Count hard links outside the original and its ``_flat`` names.

        Args:
            path: Original file.
            flat: Its ``_flat`` names.

        Returns:
            Number of further names that would keep the data alive.
        """
        return max(0, os.stat(path).st_nlink - 1 - len(flat))

    @classmethod
    def kept_list(cls, datadir: DataDir | None = None) -> KeptList:
        """Return the default kept list ``<data>/state/triage_kept.tsv``.

        Args:
            datadir: Data directory; the default one if None.

        Returns:
            The kept list object.
        """
        return KeptList(os.path.join((datadir or DataDir()).sub("state"), "triage_kept.tsv"))
