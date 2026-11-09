"""`mediatools shrink` - re-encode videos to HEVC and record them."""
from __future__ import annotations

import contextlib
import logging
import os
import shutil
import signal
import time
from collections.abc import Callable

from mediatools.core.codeccache import CodecCache
from mediatools.core.durationcache import DurationCache
from mediatools.core.mediatypes import MediaTypes
from mediatools.core.probe import MediaProbe
from mediatools.core.progressbar import ProgressBar
from mediatools.core.runcontrol import RunControl
from mediatools.core.walker import TreeWalker
from mediatools.shrink.database import ShrinkDatabase
from mediatools.shrink.encoder import HevcEncoder
from mediatools.shrink.options import ShrinkOptions
from mediatools.shrink.fileresult import FileResult
from mediatools.shrink.shrinkstate import ShrinkState
from mediatools.shrink.throughput import ThroughputLog


class Shrinker:
    """Shrinks the videos below a directory and keeps their file names.

    A run has two phases:

    1. **Plan** - walk the tree, finish replacements interrupted by a crash
       (journal, see below), note leftover ``newShrinkedFiles/`` folders of
       earlier aborted runs, and collect every video that is not yet
       recorded in its directory's database. The database always has
       precedence: a recorded file counts as processed, whatever its codec.
       Optionally drop videos shorter than ``min_seconds``, set aside videos
       that are already HEVC (recorded after the start question, never
       encoded, not counted in volume and time) and sort the rest
       (``largest`` first, ``smallest`` first or walk order ``path``).
    2. **Encode** - per file: already HEVC -> record only; otherwise
       :class:`HevcEncoder` writes to ``newShrinkedFiles/``; only a validated
       result replaces the original (in place, the inode is kept, so
       ``_flat`` hard links see the new content) and is recorded.

    **Stopping.** The first Ctrl+C (or Ctrl+Z), or ``mediatools stop`` from
    another terminal (see :class:`RunControl`), lets the current file finish
    and then stops; the second Ctrl+C, SIGTERM or SIGHUP (terminal closed)
    aborts the current encode at once - its partial output is deleted, the
    original is untouched. Signals never interrupt the replacement itself.

    **Crash safety.** The validated output is flushed to disk, then a
    marker ``newShrinkedFiles/<name>.replace`` is written and flushed, and
    the directory entry is flushed; only then is the original overwritten.
    The marker is removed after the copy has been flushed and recorded. If
    the machine fails in between, the next run finds the marker next to the
    complete output and repeats the copy before anything else.

    **Leftovers.** ``newShrinkedFiles/`` folders that remain after recovery
    hold only partial outputs of aborted runs. They are listed in the plan
    summary and deleted only after the start question is confirmed.

    With ``keep_copies=True`` the originals are not touched and nothing is
    recorded; the shrunk copies stay in ``newShrinkedFiles/`` for review.

    Attributes:
        root: Absolute start directory.
        log: Logger.
        opts: What is processed and how (:class:`ShrinkOptions`).
        probe: Probe for codec and duration checks.
        encoder: Encoder for new files.
        state: Persistent helpers: ``durations`` (for ``min_seconds``) and
            ``codecs`` (HEVC check of the plan) - None probes without caching -
            and ``throughput`` (speed and size ratio for the estimates).
        control: Stop requests from ``mediatools stop``, or None.
        databases: Open databases per directory.
        flags: ``stop`` (finish the current file, then stop) and ``abort``
            (stop the current file at once).
        display: Progress display of the encode phase.
        stats: Counters ``shrunk``, ``hevc``, ``skipped``, ``short``,
            ``failed``, ``copies``, ``recovered``.
        found: ``failed`` - paths that could not be encoded; ``leftovers`` -
            ``(folder, files, bytes)`` of leftover work folders; ``hevc`` -
            ``(path, size)`` of planned videos that are already HEVC.
    """

    MARKER = ".replace"
    CHECKPOINT = 500

    def __init__(self, root: str, logger, opts: ShrinkOptions | None = None,
                 encoder: HevcEncoder | None = None, probe: MediaProbe | None = None,
                 durations: DurationCache | None = None,
                 throughput: ThroughputLog | None = None, display: ProgressBar | None = None,
                 codecs: CodecCache | None = None):
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
            codecs: Codec cache for the plan; None probes without caching.
        """
        self.root = os.path.abspath(root)
        self.log = logger
        self.opts = opts or ShrinkOptions()
        self.probe = probe or MediaProbe()
        self.encoder = encoder or HevcEncoder(probe=self.probe, logger=logger)
        self.state: ShrinkState = {"durations": durations, "codecs": codecs,
                                   "throughput": throughput or ThroughputLog(),
                                   "results": None}
        self.control: RunControl | None = None
        self.databases: dict[str, ShrinkDatabase] = {}
        self.flags = {"stop": False, "abort": False}
        self.display = display
        self.stats = {"shrunk": 0, "hevc": 0, "skipped": 0, "short": 0, "failed": 0,
                      "copies": 0, "recovered": 0}
        self.found: dict[str, list] = {"failed": [], "leftovers": [], "hevc": []}

    # -- signals ---------------------------------------------------------
    def install_signal_handlers(self) -> None:
        """Route SIGINT, SIGTERM, SIGHUP and SIGTSTP to :meth:`on_signal`."""
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGTSTP):
            signal.signal(sig, self.on_signal)

    def on_signal(self, signum: int, _frame: object = None) -> None:
        """First Ctrl+C: stop after this file; second Ctrl+C, SIGTERM, SIGHUP: abort now.

        Only flags are set and ffmpeg is told to stop; no exception is
        raised, so a running replacement always completes. After SIGHUP the
        terminal is gone, so the progress line is switched off.

        Args:
            signum: Number of the received signal.
        """
        if signum == signal.SIGHUP and self.display:
            self.display.enabled = False
        if self.display:
            self.display.clear()
        if signum in (signal.SIGTERM, signal.SIGHUP) or \
                (signum == signal.SIGINT and self.flags["stop"]):
            self.flags["abort"] = self.flags["stop"] = True
            self.log.warning("aborting the current file now - its original stays untouched")
            self.encoder.abort()
            return
        self._mark_stopping("stopping after the current file (press Ctrl+C again to abort it)")

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
                self._note_leftover(dirpath)
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
        todo = self._set_aside_hevc(todo)
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
            if number % self.CHECKPOINT == 0 and number < len(todo):
                self._checkpoint("durations", self.state["durations"], number, len(todo))
            duration = self._duration(path)
            if duration is not None and duration < self.opts.min_seconds:
                self.stats["short"] += 1
            else:
                kept.append((path, size))
        self._checkpoint("durations", self.state["durations"], len(todo), len(todo))
        return kept

    def _set_aside_hevc(self, todo: list[tuple[str, int]]) -> list[tuple[str, int]]:
        """Move videos that are already HEVC out of the encode list.

        Args:
            todo: Candidates.

        Returns:
            Candidates that need encoding; the others go to
            ``found["hevc"]``.
        """
        rest = []
        for number, (path, size) in enumerate(todo, 1):
            if number % self.CHECKPOINT == 0 and number < len(todo):
                self._checkpoint("codecs", self.state["codecs"], number, len(todo))
            if self._codec(path) == "hevc":
                self.found["hevc"].append((path, size))
            else:
                rest.append((path, size))
        self._checkpoint("codecs", self.state["codecs"], len(todo), len(todo))
        return rest

    def _checkpoint(self, what: str, cache: DurationCache | None,
                    number: int, total: int) -> None:
        """Report planning progress and store newly probed values.

        Saving every :attr:`CHECKPOINT` files keeps the probes of a long
        first check even if the run is interrupted during planning.

        Args:
            what: ``durations`` or ``codecs``.
            cache: The cache in use, or None.
            number: Files checked so far.
            total: Files to check.
        """
        if total == 0:
            return
        if cache is None:
            self.log.info("checking %s: %i of %i", what, number, total)
            return
        self.log.info("checking %s: %i of %i (%i from cache)", what, number, total, cache.hits)
        if cache.added:
            cache.save()

    def _codec(self, path: str) -> str | None:
        """Return a video's codec, from the cache if possible.

        Args:
            path: Video file.

        Returns:
            Codec name, or None if unknown.
        """
        if self.state["codecs"] is None:
            return self.probe.video_codec(path)
        st = os.stat(path)
        codec = self.state["codecs"].get(path, st)
        if codec is None:
            codec = self.probe.video_codec(path)
            if codec is not None:
                self.state["codecs"].put(path, st, codec)
        return codec

    def _duration(self, path: str) -> float | None:
        """Return a video's duration, from the cache if possible.

        Args:
            path: Video file.

        Returns:
            Seconds, or None if unknown.
        """
        if self.state["durations"] is None:
            return self.probe.duration(path)
        st = os.stat(path)
        duration = self.state["durations"].get(path, st)
        if duration is None:
            duration = self.probe.duration(path)
            if duration is not None:
                self.state["durations"].put(path, st, duration)
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
        if self.control is not None:
            self.control.start()
        try:
            return self._run(confirm)
        finally:
            if self.control is not None:
                self.control.finish()
            if self.state["results"] is not None:
                self.state["results"].finish()

    def _run(self, confirm: Callable[[], bool] | None) -> int:
        """Body of :meth:`run`.

        Args:
            confirm: See :meth:`run`.

        Returns:
            See :meth:`run`.
        """
        mode = "keep copies" if self.opts.keep_copies else "replace originals"
        self.log.info("shrink root: %s  mode: %s  order: %s", self.root, mode, self.opts.order)
        todo = self.plan()
        total = sum(size for _path, size in todo)
        rate = self.state["throughput"].rate()
        self._report_plan(todo, total, rate)
        if (todo or self.found["leftovers"] or self.found["hevc"]) and confirm is not None \
                and not confirm():
            if self.found["leftovers"]:
                self.log.info("leftover work folders kept")
            self._finish()
            return 0
        if self.control is not None and todo:
            self.control.begin_work()                # background, sleep lock
        self._remove_leftovers()
        self._record_hevc()
        if self.display is None:
            self.display = ProgressBar(total, len(todo), rate)
        if self.control is not None:
            self.display.status_path = self.control.status_file
        self._attach_bar_filter()
        for path, size in todo:
            self._poll_stop(force=True)
            if self.flags["stop"]:
                break
            if self._process(path, size):
                self.display.file_done(size)
            else:
                self.display.file_skipped(size)
        self.display.clear()
        if self.state["results"] is not None:
            self.state["results"].finish()           # close the table before the summary
        self._finish()
        if self.flags["stop"]:
            return 1
        return 2 if self.found["failed"] else 0

    def _report_plan(self, todo: list[tuple[str, int]], total: int,
                     rate: float | None) -> None:
        """Log the plan summary with time and size estimates and leftovers.

        Args:
            todo: Planned videos.
            total: Their total size in bytes.
            rate: Measured speed in source bytes per second, or None.
        """
        estimate = f", about {ThroughputLog.duration_text(total / rate)} at the measured " \
                   f"{rate / 1e6:.1f} MB/s" if rate and total else ""
        hevc = self.found["hevc"]
        self.log.info("already processed (database): %i | shorter than %.0f s: %i | "
                      "already HEVC, recorded without encoding: %i files, %.1f GiB",
                      self.stats["skipped"], self.opts.min_seconds, self.stats["short"],
                      len(hevc), sum(size for _path, size in hevc) / 1024 ** 3)
        self.log.info("to encode: %i files, %.1f GiB%s", len(todo), total / 1024 ** 3,
                      estimate)
        ratio = self.state["throughput"].ratio()
        if ratio is not None and total:
            self.log.info("expected afterwards: about %.1f GiB, saving about %.1f GiB "
                          "(output %.0f %% of the source, measured on %i file(s))",
                          total * ratio / 1024 ** 3, total * (1 - ratio) / 1024 ** 3,
                          ratio * 100, self.state["throughput"].files)
        elif total:
            self.log.info("expected size afterwards: not yet known (measured after the "
                          "first encoded file)")
        leftovers = self.found["leftovers"]
        if leftovers:
            files = sum(entry[1] for entry in leftovers)
            size = sum(entry[2] for entry in leftovers)
            self.log.info("leftover work folders of aborted runs: %i folder(s), %i file(s), "
                          "%.1f GiB - deleted after confirmation", len(leftovers), files,
                          size / 1024 ** 3)
            for folder, count, nbytes in leftovers:
                self.log.debug("  leftover: %s (%i files, %.0f MB)", folder, count, nbytes / 1e6)

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

    def _process(self, path: str, size: int) -> bool:
        """Handle one planned video.

        Args:
            path: Absolute path of the video.
            size: Its size when planned.

        Returns:
            True if an encode was attempted, False if the file needed none
            (removed, recorded meanwhile, or HEVC by now - e.g. a hard link
            of a file shrunk earlier in this run).
        """
        dirpath = os.path.dirname(path)
        database = self.database(dirpath)
        if not os.path.isfile(path) or database.is_done(path):
            return False                             # removed or recorded meanwhile
        if self.probe.video_codec(path) == "hevc":
            self._record_one_hevc(path)
            return False
        tmp_dir = os.path.join(dirpath, MediaTypes.SHRINK_TMP_DIR)
        os.makedirs(tmp_dir, exist_ok=True)
        out = os.path.join(tmp_dir, os.path.basename(path))
        try:
            self._encode_and_replace(path, size, out, database)
        finally:
            with contextlib.suppress(OSError):
                os.rmdir(tmp_dir)                    # only if empty (not in review mode)
        return True

    def _record_hevc(self) -> None:
        """Record the videos set aside as HEVC during planning."""
        if self.opts.keep_copies:
            return                                   # review mode records nothing
        for path, _size in self.found["hevc"]:
            if self.flags["stop"]:
                break
            if os.path.isfile(path) and not self.database(os.path.dirname(path)).is_done(path):
                self._record_one_hevc(path)

    def _record_one_hevc(self, path: str) -> None:
        """Record a video that is already HEVC, without encoding it.

        Args:
            path: Video file.
        """
        self.log.debug("already HEVC, recorded without re-encoding: %s", path)
        database = self.database(os.path.dirname(path))
        database.ensure()
        database.record(path)
        self.stats["hevc"] += 1
        self._result(FileResult(path, "HEVC", os.path.getsize(path)))

    def _encode_and_replace(self, path: str, size: int, out: str,
                            database: ShrinkDatabase) -> None:
        """Encode one video and, if valid, replace the original.

        Args:
            path: Original video.
            size: Its size.
            out: Output path in ``newShrinkedFiles/``.
            database: Database of its directory.
        """
        self.log.debug("shrinking (%.0f MB): %s", size / 1e6, path)
        name = os.path.basename(path)
        length = (self.state["durations"].get(path, os.stat(path))
                  if self.state["durations"] is not None else None)
        started = time.monotonic()
        ok = self.encoder.encode(path, out, lambda fraction: self._show(name, fraction, size))
        if self.flags["abort"]:
            self._remove_quietly(out)
            self._remove_quietly(out + ".ffmpeg.log")
            return
        if not ok:
            self.found["failed"].append(path)
            self.stats["failed"] += 1
            self._remove_quietly(out)
            self._remove_quietly(out + ".ffmpeg.log")      # its tail is in the log
            self._result(FileResult(path, "FAILED", size, None, length,
                                    time.monotonic() - started))
            return
        out_size = self._size_or_none(out)
        took = time.monotonic() - started
        self.state["throughput"].add(size, took, out_size)
        if self.opts.keep_copies:
            self.stats["copies"] += 1
            self._result(FileResult(path, "copy", size, out_size, length, took))
            return
        self._replace(out, path, database)
        self.stats["shrunk"] += 1
        self._result(FileResult(path, "shrunk", size, out_size, length, took))

    def _result(self, item: FileResult) -> None:
        """Add a row to the result table, if one is set.

        Args:
            item: Outcome of one video.
        """
        table = self.state["results"]
        if table is None:
            return
        if self.display:
            self.display.clear()
        table.add(item)

    def _show(self, name: str, fraction: float, size: int) -> None:
        """Forward encoder progress to the display.

        Args:
            name: Current file name.
            fraction: Encoded fraction of the file.
            size: Its size in bytes.
        """
        self._poll_stop()
        if self.display:
            self.display.show(name, fraction, size)

    def _poll_stop(self, force: bool = False) -> None:
        """Turn a request of ``mediatools stop`` into a stop after this file.

        Args:
            force: Check the stop file now, ignoring the poll interval.
        """
        if self.control is None or self.flags["stop"]:
            return
        if self.control.stop_requested(force):
            self._mark_stopping("stop requested by 'mediatools stop' - "
                                "stopping after the current file")

    def _mark_stopping(self, message: str) -> None:
        """Set the stop flag and show it in the log and on the progress line.

        Args:
            message: Log message.
        """
        self.flags["stop"] = True
        if self.display:
            self.display.clear()
            self.display.note = "STOPPING after this file"
        self.log.warning(message)

    # -- replacement with journal ----------------------------------------
    def _replace(self, out: str, path: str, database: ShrinkDatabase) -> None:
        """Copy a validated output over the original and record it.

        Order: flush the output, write and flush the marker, flush the
        directory entry, copy into the existing inode, flush, record, delete
        output, delete marker. A crash at any point leaves either the
        untouched original or the marker plus the complete validated output;
        repeating the copy is harmless, so a crash after step 5 only causes
        one more identical copy on the next run.

        Args:
            out: Validated output in ``newShrinkedFiles/``.
            path: Original video.
            database: Database of its directory.
        """
        marker = out + self.MARKER
        self._fsync_file(out)                        # 1. output complete on disk
        with open(marker, "w", encoding="utf-8") as handle:
            handle.write(path + "\n")
            handle.flush()
            os.fsync(handle.fileno())                # 2. marker content on disk
        self._fsync_dir(os.path.dirname(marker))     # 3. marker's directory entry on disk
        shutil.copyfile(out, path)                   # 4. writes into the existing inode
        self._fsync_file(path)                       # 5. new content of the original on disk
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
    def _note_leftover(self, dirpath: str) -> None:
        """Remember a leftover work folder for the plan summary.

        Args:
            dirpath: Directory whose ``newShrinkedFiles`` is checked.
        """
        tmp = os.path.join(dirpath, MediaTypes.SHRINK_TMP_DIR)
        if not os.path.isdir(tmp):
            return
        files = nbytes = 0
        for base, _dirs, names in os.walk(tmp):
            for name in names:
                files += 1
                nbytes += self._size_or_none(os.path.join(base, name)) or 0
        self.found["leftovers"].append((tmp, files, nbytes))

    def _remove_leftovers(self) -> None:
        """Delete the leftover work folders noted during planning."""
        for tmp, _files, _nbytes in self.found["leftovers"]:
            self.log.info("removing leftover work folder %s", tmp)
            shutil.rmtree(tmp, ignore_errors=True)
        self.found["leftovers"] = []

    @staticmethod
    def _size_or_none(path: str) -> int | None:
        """Return a file's size.

        Args:
            path: File.

        Returns:
            Size in bytes, or None if it cannot be read.
        """
        try:
            return os.path.getsize(path)
        except OSError:
            return None

    @staticmethod
    def _fsync_file(path: str) -> None:
        """Force a file's content to disk.

        Args:
            path: File.
        """
        with open(path, "rb+") as handle:
            os.fsync(handle.fileno())

    @staticmethod
    def _fsync_dir(path: str) -> None:
        """Force a directory's entries (file creations) to disk.

        Args:
            path: Directory.
        """
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

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
        for path in self.found["failed"]:
            self.log.info("  FAILED: %s", path)
