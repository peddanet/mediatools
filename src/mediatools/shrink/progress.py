"""`mediatools progress` - read-only report of the shrink status."""
from __future__ import annotations

import os

from mediatools.core.datadir import DataDir
from mediatools.core.mediatypes import MediaTypes
from mediatools.core.walker import TreeWalker
from mediatools.shrink.database import ShrinkDatabase


class ProgressReport:
    """Lists which videos are recorded in their shrink database and which not.

    Strictly read-only: no database is created and nothing is deleted
    (it never creates or deletes anything). Two lists are
    written to ``<data>/reports/``: ``progress_done_<ts>.txt`` and
    ``progress_todo_<ts>.txt``.

    Attributes:
        root: Absolute start directory.
        log: Logger.
        datadir: Data directory for the reports.
        extensions: Extensions that are counted.
        db_name: Database file name (None: default name).
        done: Recorded videos.
        todo: Videos not recorded yet.
    """

    def __init__(self, root: str, logger, datadir: DataDir | None = None,
                 extensions: frozenset = frozenset({".mp4"}), db_name: str | None = None):
        """Configure the report.

        Args:
            root: Start directory.
            logger: :class:`logging.Logger`.
            datadir: Data directory; the default one if None.
            extensions: Extensions that are counted.
            db_name: Database file name; the default name if None.
        """
        self.root = os.path.abspath(root)
        self.log = logger
        self.datadir = datadir or DataDir()
        self.extensions = extensions
        self.db_name = db_name
        self.done: list[str] = []
        self.todo: list[str] = []

    def scan(self) -> None:
        """Fill :attr:`done` and :attr:`todo` by walking the tree."""
        for dirpath, _dirs, files in TreeWalker().walk(self.root):
            database = ShrinkDatabase(dirpath, name=self.db_name)   # read only
            for name in files:
                if MediaTypes.extension(name) not in self.extensions:
                    continue
                path = os.path.join(dirpath, name)
                if os.path.islink(path) or os.path.getsize(path) == 0:
                    continue
                (self.done if database.is_done(path) else self.todo).append(path)

    def write(self) -> tuple[str, str]:
        """Write both lists to the reports directory.

        Returns:
            Paths of the done list and the todo list.
        """
        done_path = self.datadir.file("reports", "progress_done", ".txt")
        todo_path = done_path.replace("progress_done", "progress_todo")
        for path, items in ((done_path, self.done), (todo_path, self.todo)):
            with open(path, "w", encoding="utf-8") as handle:
                handle.writelines(item + "\n" for item in items)
        return done_path, todo_path

    def run(self) -> int:
        """Scan, write the lists and log a summary.

        Returns:
            Always 0.
        """
        self.scan()
        done_path, todo_path = self.write()
        total = len(self.done) + len(self.todo)
        self.log.info("videos: %i | recorded (done): %i | not recorded (todo): %i",
                      total, len(self.done), len(self.todo))
        self.log.info("done list: %s", done_path)
        self.log.info("todo list: %s", todo_path)
        return 0
