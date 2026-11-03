"""`mediatools shrink` - re-encode videos to HEVC and record them."""
from __future__ import annotations

import os
import shutil
import signal

from mediatools.core.mediatypes import MediaTypes
from mediatools.core.probe import MediaProbe
from mediatools.core.walker import TreeWalker
from mediatools.shrink.database import ShrinkDatabase
from mediatools.shrink.encoder import HevcEncoder


class Shrinker:
    """Shrinks all videos below a directory and keeps their file names.

    Decision per file, in this order:

    1. **Database** - path, size and mtime match a row: skip. The database
       always has precedence.
    2. **Codec** - the first video stream is already HEVC: not re-encoded,
       recorded as done.
    3. **Encode** - :class:`HevcEncoder` writes to ``newShrinkedFiles/``;
       only a validated result is copied over the original (in place, the
       inode is kept, so hard links such as ``_flat`` entries see the new
       content) and recorded.

    With ``keep_copies=True`` the originals are not touched and nothing is
    recorded; the shrunk copies stay in ``newShrinkedFiles/`` for review.

    Attributes:
        root: Absolute start directory.
        log: Logger.
        keep_copies: Review mode, see above.
        db_name: Database file name (None: default name).
        extensions: Extensions that are processed.
        probe: Probe for the codec check.
        encoder: Encoder for new files.
        walker: Tree walker with the shared skip rules.
        stop_requested: Set by the signal handler.
        stats: Counters ``shrunk``, ``hevc``, ``skipped``, ``failed``, ``copies``.
        failed: Paths that could not be encoded.
    """

    def __init__(self, root: str, logger, keep_copies: bool = False,
                 extensions: frozenset = frozenset({".mp4"}),
                 encoder: HevcEncoder | None = None, probe: MediaProbe | None = None,
                 db_name: str | None = None):
        """Configure a run.

        Args:
            root: Start directory, processed recursively.
            logger: :class:`logging.Logger` for progress and results.
            keep_copies: Do not replace originals, record nothing.
            extensions: Extensions to process.
            encoder: Encoder; a default :class:`HevcEncoder` if None.
            probe: Probe; a new :class:`MediaProbe` if None.
            db_name: Database file name; the default name if None.
        """
        self.root = os.path.abspath(root)
        self.log = logger
        self.keep_copies = keep_copies
        self.db_name = db_name
        self.extensions = extensions
        self.probe = probe or MediaProbe()
        self.encoder = encoder or HevcEncoder(probe=self.probe, logger=logger)
        self.walker = TreeWalker()
        self.stop_requested = False
        self.stats = {"shrunk": 0, "hevc": 0, "skipped": 0, "failed": 0, "copies": 0}
        self.failed: list[str] = []

    # -- signals ---------------------------------------------------------
    def install_signal_handlers(self) -> None:
        """Defer SIGINT, SIGTERM and SIGTSTP until the current file is done."""
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGTSTP):
            signal.signal(sig, self._on_signal)

    def _on_signal(self, signum, _frame) -> None:
        """Remember that the user wants to stop.

        Args:
            signum: Number of the received signal.
            _frame: Current stack frame (unused).
        """
        self.log.info("signal %s received - stopping after the current file",
                      signal.Signals(signum).name)
        self.stop_requested = True

    # -- main loop -------------------------------------------------------
    def run(self) -> int:
        """Process the whole tree.

        Returns:
            0 on success, 1 if interrupted, 2 if at least one file failed.
        """
        self.log.info("shrink root: %s  mode: %s", self.root,
                      "keep copies" if self.keep_copies else "replace originals")
        for dirpath, _dirs, files in self.walker.walk(self.root):
            videos = [f for f in files if MediaTypes.extension(f) in self.extensions]
            if not self.keep_copies:
                # leftovers of an earlier review run are obsolete
                self._remove_tmp(dirpath)
            if not videos:
                continue
            database = ShrinkDatabase(dirpath, self.log, self.db_name)
            database.ensure()
            for name in videos:
                path = os.path.join(dirpath, name)
                if os.path.islink(path) or not os.path.isfile(path):
                    continue
                if not self._process(database, dirpath, path):
                    self._finish()
                    return 1
            if not self.keep_copies:
                self._remove_tmp(dirpath)
        self._finish()
        return 2 if self.failed else 0

    def _process(self, database: ShrinkDatabase, dirpath: str, path: str) -> bool:
        """Handle one video.

        Args:
            database: Database of the video's directory.
            dirpath: Directory of the video.
            path: Absolute path of the video.

        Returns:
            False if the run must stop (signal received), otherwise True.
        """
        if database.is_done(path):
            self.log.info("in database, skipped: %s", path)
            self.stats["skipped"] += 1
            return True
        if self.probe.video_codec(path) == "hevc":
            self.log.info("already HEVC, recorded without re-encoding: %s", path)
            database.record(path)
            self.stats["hevc"] += 1
            return True

        tmp_dir = os.path.join(dirpath, MediaTypes.SHRINK_TMP_DIR)
        os.makedirs(tmp_dir, exist_ok=True)
        out = os.path.join(tmp_dir, os.path.basename(path))
        self.log.info("shrinking: %s", path)
        ok = self.encoder.encode(path, out)

        if self.stop_requested:
            self.log.info("stop requested; original untouched: %s", path)
            self._remove_quietly(out)
            return False
        if not ok:
            self.failed.append(path)
            self.stats["failed"] += 1
            self._remove_quietly(out)
            return True
        if self.keep_copies:
            self.stats["copies"] += 1
            return True

        # critical section: copy back and record, no stop in between
        shutil.copyfile(out, path)           # writes into the existing inode
        database.record(path)
        self.stats["shrunk"] += 1
        return not self.stop_requested

    # -- helpers ---------------------------------------------------------
    def _remove_tmp(self, dirpath: str) -> None:
        """Delete the temporary output directory of one directory.

        Args:
            dirpath: Directory whose ``newShrinkedFiles`` is removed.
        """
        tmp = os.path.join(dirpath, MediaTypes.SHRINK_TMP_DIR)
        if os.path.isdir(tmp):
            self.log.info("removing %s", tmp)
            shutil.rmtree(tmp, ignore_errors=True)

    @staticmethod
    def _remove_quietly(path: str) -> None:
        """Delete a file if it exists.

        Args:
            path: File to delete.
        """
        try:
            os.remove(path)
        except FileNotFoundError:
            pass

    def _finish(self) -> None:
        """Remove all temporary directories below root and log the summary."""
        if not self.keep_copies:
            for dirpath, _dirs, _files in self.walker.walk(self.root):
                self._remove_tmp(dirpath)
        counts = self.stats
        self.log.info("=" * 70)
        self.log.info("shrunk and recorded: %i | already HEVC: %i | skipped (DB): %i | "
                      "copies kept: %i | failed: %i", counts["shrunk"], counts["hevc"],
                      counts["skipped"], counts["copies"], counts["failed"])
        for path in self.failed:
            self.log.info("  FAILED: %s", path)
