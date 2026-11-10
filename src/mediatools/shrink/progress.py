"""`mediatools progress` - read-only report of the shrink status."""
from __future__ import annotations

import os

from mediatools.core.datadir import DataDir
from mediatools.core.mediatypes import MediaTypes
from mediatools.core.walker import TreeWalker
from mediatools.shrink.database import ShrinkDatabase
from mediatools.shrink.throughput import ThroughputLog


class ProgressReport:
    """Reports which videos are recorded in their shrink database and which not.

    Strictly read-only: no database is created and nothing is changed.
    Matching follows :meth:`ShrinkDatabase.status` (file name within the
    directory), so databases written at an earlier location of the
    collection are evaluated correctly. Four lists are written to
    ``<data>/reports/``: ``progress_done``, ``progress_changed`` (recorded,
    size differs - counts as processed), ``progress_regrown`` (recorded,
    but at least 20 % larger again - ``shrink`` encodes it again unless it
    is HEVC) and ``progress_todo``. Regrown videos count towards the bytes
    still to do.

    Attributes:
        root: Absolute start directory.
        log: Logger.
        datadir: Data directory for the reports.
        extensions: Extensions that are counted.
        db_name: Database file name (None: default name).
        throughput: Measured encoding speed for the time estimate.
        lists: Paths per status ``done``, ``changed``, ``regrown``, ``todo``.
        todo_bytes: Total size of the videos still to do (todo and regrown).
        databases: Number of databases found.
        orphan_rows: Database rows without a matching file.
    """

    STATES = ("done", "changed", "regrown", "todo")

    def __init__(self, root: str, logger, datadir: DataDir | None = None,
                 extensions: frozenset = frozenset({".mp4"}), db_name: str | None = None,
                 throughput: ThroughputLog | None = None):
        """Configure the report.

        Args:
            root: Start directory.
            logger: :class:`logging.Logger`.
            datadir: Data directory; the default one if None.
            extensions: Extensions that are counted.
            db_name: Database file name; the default name if None.
            throughput: Speed measurements; loaded from the data directory if None.
        """
        self.root = os.path.abspath(root)
        self.log = logger
        self.datadir = datadir or DataDir()
        self.extensions = extensions
        self.db_name = db_name
        self.throughput = throughput or ThroughputLog(
            os.path.join(self.datadir.sub("state"), "shrink_throughput.json"))
        self.lists: dict[str, list[str]] = {state: [] for state in self.STATES}
        self.todo_bytes = 0
        self.databases = 0
        self.orphan_rows = 0

    def scan(self) -> None:
        """Fill :attr:`lists` by walking the tree."""
        for dirpath, _dirs, files in TreeWalker().walk(self.root):
            database = ShrinkDatabase(dirpath, name=self.db_name)   # read only
            if database.exists():
                self.databases += 1
            present = set()
            for name in files:
                if MediaTypes.extension(name) not in self.extensions:
                    continue
                path = os.path.join(dirpath, name)
                if os.path.islink(path) or os.path.getsize(path) == 0:
                    continue
                present.add(name)
                state = database.status(path)
                self.lists[state].append(path)
                if state in ("todo", "regrown"):
                    self.todo_bytes += os.path.getsize(path)
            self.orphan_rows += sum(1 for name in database.index() if name not in present)

    def write(self) -> dict[str, str]:
        """Write the four lists to the reports directory.

        Returns:
            Report path per status.
        """
        first = self.datadir.file("reports", "progress_done", ".txt")
        paths = {state: first.replace("progress_done", f"progress_{state}")
                 for state in self.STATES}
        for state, path in paths.items():
            with open(path, "w", encoding="utf-8", errors="surrogateescape") as handle:
                handle.writelines(item + "\n" for item in self.lists[state])
        return paths

    def run(self) -> int:
        """Scan, write the lists and log a summary with a time estimate.

        Returns:
            Always 0.
        """
        self.scan()
        paths = self.write()
        done, changed, regrown, todo = (len(self.lists[s]) for s in self.STATES)
        self.log.info("videos: %i | processed: %i (of which size changed since: %i) | "
                      "regrown: %i | todo: %i | to encode: %.1f GiB",
                      done + changed + regrown + todo, done + changed, changed, regrown,
                      todo, self.todo_bytes / 1024 ** 3)
        self.log.info("databases: %i | rows without a matching file: %i", self.databases,
                      self.orphan_rows)
        rate = self.throughput.rate()
        todo += regrown
        if rate and todo:
            self.log.info("estimated encoding time at the measured %.1f MB/s: about %s",
                          rate / 1e6, ThroughputLog.duration_text(self.todo_bytes / rate))
        elif todo:
            self.log.info("no speed measured yet - the first shrink run measures it")
        for state in self.STATES:
            self.log.info("%s list: %s", state, paths[state])
        return 0
