"""Per-run logging to terminal and file."""
from __future__ import annotations

import logging
import sys

from mediatools.core.datadir import DataDir


class RunLog:
    """Logger that writes to stdout and to a time-stamped log file.

    The terminal shows plain messages; the file additionally contains
    timestamp and level of every line.

    Example:
        >>> run = RunLog("shrink")
        >>> run.logger.info("started")

    Attributes:
        datadir: Data directory in which the log file is created.
        path: Absolute path of the log file.
        logger: The configured :class:`logging.Logger`.
    """

    def __init__(self, name: str, verbose: bool = False, datadir: DataDir | None = None):
        """Create the logger and its two handlers.

        Args:
            name: Command name; used in the logger and file name.
            verbose: If True, DEBUG messages are logged as well.
            datadir: Data directory; the default one if None.
        """
        self.datadir = datadir or DataDir()
        self.path = self.datadir.file("logs", name, ".log")
        # unique logger name, so several runs in one process do not share handlers
        self.logger = logging.getLogger(f"mediatools.{name}.{id(self)}")
        # id() values can be reused after garbage collection: close stale
        # handlers of a recycled logger instead of only dropping them
        for stale in list(self.logger.handlers):
            stale.close()
            self.logger.removeHandler(stale)
        self.logger.setLevel(logging.DEBUG if verbose else logging.INFO)
        self.logger.propagate = False

        file_handler = logging.FileHandler(self.path, mode="w", encoding="utf-8")
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(logging.Formatter("%(message)s"))
        self.logger.addHandler(file_handler)
        self.logger.addHandler(stream_handler)
        self.logger.info("log file: %s", self.path)

    def close(self) -> None:
        """Flush and close all handlers (important in tests)."""
        for handler in list(self.logger.handlers):
            handler.close()
            self.logger.removeHandler(handler)
