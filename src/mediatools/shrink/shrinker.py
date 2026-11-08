"""`mediatools shrink` - re-encode videos to HEVC and record them."""
from __future__ import annotations

import contextlib
import logging
import os
import shutil
import signal
import time
from collections.abc import Callable

from mediatools.core.durationcache import DurationCache
from mediatools.core.mediatypes import MediaTypes
from mediatools.core.probe import MediaProbe
from mediatools.core.progressbar import ProgressBar
from mediatools.core.walker import TreeWalker
from mediatools.shrink.database import ShrinkDatabase
from mediatools.shrink.encoder import HevcEncoder
from mediatools.shrink.options import ShrinkOptions
from mediatools.shrink.throughput import ThroughputLog


class Shrinker:
    """Shrinks the videos below a directory and keeps their file names.

    A run has two phases:

    1. **Plan** - walk the tree, finish replacements interrupted by a crash
       (journal, see below), and collect every video that is not yet
       recorded in its directory's database. The database always has
       precedence: a recorded file counts as processed, whatever its codec.
       Optionally drop videos shorter than ``min_seconds`` and sort the rest
       (``largest`` first, ``smallest`` first or walk order ``path``).
    2. **Encode** - per file: already HEVC -> record only; otherwise
       :class:`HevcEncoder` writes to ``newShrinkedFiles/``; only a validated
       result replaces the original (in place, the inode is kept, so
       ``_flat`` hard links see the new content) and is recorded.

    **Stopping.** The first Ctrl+C (or Ctrl+Z) lets the current file finish
    and then stops; the second Ctrl+C (or SIGTERM) aborts the current
    encode at once - its partial output is deleted, the original is
    untouched. Signals never interrupt the replacement itself.

    **Crash safety.** Before an original is overwritten, a marker
    ``newShrinkedFiles/<name>.replace`` is written; it is removed after the
    copy has been flushed to disk and recorded. If the machine fails in
    between, the next run finds the marker next to the validated output and
    repeats the copy before anything else.

    With ``keep_copies=True`` the originals are not touched and nothing is
    recorded; the shrunk copies stay in ``newShrinkedFiles/`` for review.

    Attributes:
        root: Absolute start directory.
        log: Logger.
        opts: What is processed and how (:class:`ShrinkOptions`).
        probe: Probe for codec and duration checks.
        encoder: Encoder for new files.
        durations: Duration cache for ``min_seconds``.
        throughput: Speed measurements for the time estimate.
        databases: Open databases per directory.
        flags: ``stop`` (finish the current file, then stop) and ``abort``
            (stop the current file at once).
        display: Progress display of the encode phase.
        stats: Counters ``shrunk``, ``hevc``, ``skipped``, ``short``,
            ``failed``, ``copies``, ``recovered``.
        failed: Paths that could not be encoded.
    """

    MARKER = ".replace"

    def __init__(self, root: str, logger, opts: ShrinkOptions | None = None,
                 encoder: HevcEncoder | None = None, probe: MediaProbe | None = None,
                 durations: DurationCache | None = None,
                 throughput: ThroughputLog | None = None, display: ProgressBar | None = None):
        """Configure a run.

        Args:
            root: Start directory, processed recursively.
            logger: :class:`logging.Logger` for progress and results.
            opts: Run settings; defaults if None.
            encoder: Encoder; a default :class:`HevcEncoder` if None.
            probe: Probe; a new :class:`MediaProbe` if None.
            durations: Duration cache; None probes without caching.
            throughput: Speed measurements; a default one if None.
            display: Progress display; created for the encode phase if None.
        """
        self.root = os.path.abspath(root)
        self.log = logger
        self.opts = opts or ShrinkOptions()
        self.probe = probe or MediaProbe()
        self.encoder = encoder or HevcEncoder(probe=self.probe, logger=logger)
        self.durations = durations
        self.throughput = throughput or ThroughputLog()
        self.databases: dict[str, ShrinkDatabase] = {}
        self.flags = {"stop": False, "abort": False}
        self.display = display
        self.stats = {"shrunk": 0, "hevc": 0, "skipped": 0, "short": 0, "failed": 0,
                      "copies": 0, "recovered": 0}
        self.failed: list[str] = []

    # -- signals ---------------------------------------------------------
    def install_signal_handlers(self) -> None:
        """Route SIGINT, SIGTERM and SIGTSTP to :meth:`on_signal`."""
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGTSTP):
            signal.signal(sig, self.on_signal)

    def on_signal(self, signum: int, _frame: object = None) -> None:
        """First Ctrl+C: stop after this file; second Ctrl+C or SIGTERM: abort now.

        Only flags are set and ffmpeg is told to stop; no exception is
        raised, so a running replacement always completes.

        Args:
            signum: Number of the received signal.
        """
        if self.display:
            self.display.clear()
        if signum == signal.SIGTERM or (signum == signal.SIGINT and self.flags["stop"]):
            self.flags["abort"] = self.flags["stop"] = True
            self.log.warning("aborting the current file now - its original stays untouched")
            self.encoder.abort()
            return
        self.flags["stop"] = True
        self.log.warning("stopping after the current file (press Ctrl+C again to abort it)")

    # -- plan ------------------------------------------------------------
    def database(self, dirpath: str) -> ShrinkDatabase:
        """Return the (cached) database of a directory.

        Args:
            dirpath: Directory.

        Returns:
            Its :class:`ShrinkDatabase`.
        """
        if dirpath not in self.databases:
            self.databases[dirpath] = ShrinkDatabase(dirpath, self.log, self.opts.db_name)
        return self.databases[dirpath]

    def plan(self) -> list[tuple[str, int]]:
        """Recover interrupted replacements and list the videos to encode.

        Returns:
            ``(path, size)`` pairs in processing order.
        """
        todo: list[tuple[str, int]] = []
        for dirpath, _dirs, files in TreeWalker().walk(self.root):
            if not self.opts.keep_copies:
                self._recover(dirpath)
                self._remove_tmp(dirpath)
            for name in files:
                if MediaTypes.extension(name) not in self.opts.extensions:
                    continue
                path = os.path.join(dirpath, name)
                if os.path.islink(path) or not os.path.isfile(path):
                    continue
                size = os.path.getsize(path)
                if size == 0:
                    continue                     # triage placeholder
                if self.database(dirpath).is_done(path):
                    self.stats["skipped"] += 1
                else:
                    todo.append((path, size))
            if self.flags["stop"]:
                break
        if self.opts.min_seconds > 0:
            todo = self._drop_short(todo)
        if self.opts.order == "largest":
            todo.sort(key=lambda item: item[1], reverse=True)
        elif self.opts.order == "smallest":
            todo.sort(key=lambda item: item[1])
        return todo

    def _drop_short(self, todo: list[tuple[str, int]]) -> list[tuple[str, int]]:
        """Remove videos shorter than :attr:`min_seconds`.

        Args:
            todo: Candidates.

        Returns:
            Candidates with a known duration of at least ``min_seconds``
            (unknown durations are kept).
        """
        kept = []
        for number, (path, size) in enumerate(todo, 1):
            if number % 500 == 0:
                self.log.info("checking durations: %i of %i", number, len(todo))
            duration = self._duration(path)
            if duration is not None and duration < self.opts.min_seconds:
                self.stats["short"] += 1
            else:
                kept.append((path, size))
        if self.durations is not None:
            self.durations.save()
        return kept

    def _duration(self, path: str) -> float | None:
        """Return a video's duration, from the cache if possible.

        Args:
            path: Video file.

        Returns:
            Seconds, or None if unknown.
        """
        if self.durations is None:
            return self.probe.duration(path)
        st = os.stat(path)
        duration = self.durations.get(path, st)
        if duration is None:
            duration = self.probe.duration(path)
            if duration is not None:
                self.durations.put(path, st, duration)
        return duration

    # -- run -------------------------------------------------------------
    def run(self, confirm: Callable[[], bool] | None = None) -> int:
        """Plan, report, ask and encode.

        Args:
            confirm: Called after the plan summary; encoding starts only if
                it returns True. None starts without asking.

        Returns:
            0 on success or if declined, 1 if stopped by the user, 2 if a
            file failed.
        """
        mode = "keep copies" if self.opts.keep_copies else "replace originals"
        self.log.info("shrink root: %s  mode: %s  order: %s", self.root, mode, self.opts.order)
        todo = self.plan()
        total = sum(size for _path, size in todo)
        rate = self.throughput.rate()
        estimate = f", about {ThroughputLog.duration_text(total / rate)} at the measured " \
                   f"{rate / 1e6:.1f} MB/s" if rate and total else ""
        self.log.info("already processed (database): %i | shorter than %.0f s: %i | "
                      "to encode: %i files, %.1f GiB%s", self.stats["skipped"],
                      self.opts.min_seconds, self.stats["short"], len(todo), total / 1024 ** 3,
                      estimate)
        if todo and confirm is not None and not confirm():
            self._finish()
            return 0
        if self.display is None:
            self.display = ProgressBar(total, len(todo), rate)
        self._attach_bar_filter()
        for path, size in todo:
            if self.flags["stop"]:
                break
            self._process(path, size)
            self.display.file_done(size)
        self.display.clear()
        self._finish()
        if self.flags["stop"]:
            return 1
        return 2 if self.failed else 0

    def _attach_bar_filter(self) -> None:
        """Clear the progress line before each console log message."""
        display = self.display

        def clear_line(_record: logging.LogRecord) -> bool:
            """Clear the progress line; never suppresses the record.

            Returns:
                Always True.
            """
            if display:
                display.clear()
            return True

        for handler in getattr(self.log, "handlers", []):
            if isinstance(handler, logging.StreamHandler) and \
                    not isinstance(handler, logging.FileHandler):
                handler.addFilter(clear_line)

    def _process(self, path: str, size: int) -> None:
        """Handle one planned video.

        Args:
            path: Absolute path of the video.
            size: Its size when planned.
        """
        dirpath = os.path.dirname(path)
        database = self.database(dirpath)
        if not os.path.isfile(path) or database.is_done(path):
            return                                   # removed or recorded meanwhile
        if self.probe.video_codec(path) == "hevc":
            self.log.info("already HEVC, recorded without re-encoding: %s", path)
            database.ensure()
            database.record(path)
            self.stats["hevc"] += 1
            return
        tmp_dir = os.path.join(dirpath, MediaTypes.SHRINK_TMP_DIR)
        os.makedirs(tmp_dir, exist_ok=True)
        out = os.path.join(tmp_dir, os.path.basename(path))
        try:
            self._encode_and_replace(path, size, out, database)
        finally:
            with contextlib.suppress(OSError):
                os.rmdir(tmp_dir)                    # only if empty (not in review mode)

    def _encode_and_replace(self, path: str, size: int, out: str,
                            database: ShrinkDatabase) -> None:
        """Encode one video and, if valid, replace the original.

        Args:
            path: Original video.
            size: Its size.
            out: Output path in ``newShrinkedFiles/``.
            database: Database of its directory.
        """
        self.log.info("shrinking (%.0f MB): %s", size / 1e6, path)
        name = os.path.basename(path)
        started = time.monotonic()
        ok = self.encoder.encode(path, out, lambda fraction: self._show(name, fraction, size))
        if self.flags["abort"]:
            self._remove_quietly(out)
            self._remove_quietly(out + ".ffmpeg.log")
            return
        if not ok:
            self.failed.append(path)
            self.stats["failed"] += 1
            self._remove_quietly(out)
            self._remove_quietly(out + ".ffmpeg.log")      # its tail is in the log
            return
        self.throughput.add(size, time.monotonic() - started)
        if self.opts.keep_copies:
            self.stats["copies"] += 1
            return
        self._replace(out, path, database)
        self.stats["shrunk"] += 1

    def _show(self, name: str, fraction: float, size: int) -> None:
        """Forward encoder progress to the display.

        Args:
            name: Current file name.
            fraction: Encoded fraction of the file.
            size: Its size in bytes.
        """
        if self.display:
            self.display.show(name, fraction, size)

    # -- replacement with journal ----------------------------------------
    def _replace(self, out: str, path: str, database: ShrinkDatabase) -> None:
        """Copy a validated output over the original and record it.

        Order: marker, copy into the existing inode, flush to disk, record,
        delete output, delete marker. A crash at any point leaves either the
        untouched original or the marker plus the validated output.

        Args:
            out: Validated output in ``newShrinkedFiles/``.
            path: Original video.
            database: Database of its directory.
        """
        marker = out + self.MARKER
        with open(marker, "w", encoding="utf-8") as handle:
            handle.write(path + "\n")
        shutil.copyfile(out, path)                   # writes into the existing inode
        with open(path, "rb+") as handle:
            os.fsync(handle.fileno())
        database.ensure()
        database.record(path)
        os.remove(out)
        os.remove(marker)

    def _recover(self, dirpath: str) -> None:
        """Repeat replacements that a crash interrupted.

        Args:
            dirpath: Directory whose ``newShrinkedFiles`` is checked.
        """
        tmp = os.path.join(dirpath, MediaTypes.SHRINK_TMP_DIR)
        if not os.path.isdir(tmp):
            return
        for entry in sorted(os.listdir(tmp)):
            if not entry.endswith(self.MARKER):
                continue
            out = os.path.join(tmp, entry[:-len(self.MARKER)])
            target = os.path.join(dirpath, os.path.basename(out))
            if os.path.isfile(out) and os.path.isfile(target):
                self.log.warning("completing an interrupted replacement: %s", target)
                self._replace(out, target, self.database(dirpath))
                self.stats["recovered"] += 1
            else:
                self._remove_quietly(os.path.join(tmp, entry))

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
        """Log the summary."""
        counts = self.stats
        self.log.info("=" * 70)
        self.log.info("shrunk and recorded: %i | already HEVC: %i | processed before (DB): %i "
                      "| too short: %i | copies kept: %i | recovered: %i | failed: %i",
                      counts["shrunk"], counts["hevc"], counts["skipped"], counts["short"],
                      counts["copies"], counts["recovered"], counts["failed"])
        if self.flags["stop"]:
            self.log.info("stopped by the user - the next run continues with the files left")
        for path in self.failed:
            self.log.info("  FAILED: %s", path)
