"""Live view of a job running in the background (``mediatools attach``)."""
from __future__ import annotations

import os
import re
import shutil
import sys
import time
from typing import TextIO

from mediatools.core.runcontrol import RunControl


class AttachView:
    """Follow the log and the progress lines of a running job.

    Shows the last :attr:`BACKLOG` lines of the job's log file, then every
    new line as it is written - like the terminal of the run: DEBUG lines
    are left out, INFO lines without time stamp and level - and below them
    the progress lines (two, or three with the space line) from the status
    file, redrawn in place. Ctrl+C
    only leaves the view; the job keeps running. The view ends by itself
    when the job ends.

    Example:
        >>> AttachView(RunControl("shrink")).run()

    Attributes:
        control: Control files of the job.
        stream: Output stream (the terminal).
        poll: Seconds between two updates.
        drawn: Number of progress lines on the screen (0: none).
    """

    BACKLOG = 30
    LOG_LINE = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} (\w+)\s+(.*)$", re.S)

    def __init__(self, control: RunControl, stream: TextIO | None = None,
                 poll: float = 1.0) -> None:
        """Configure the view.

        Args:
            control: Control files of the job.
            stream: Output stream; stdout if None.
            poll: Seconds between two updates.
        """
        self.control = control
        self.stream = stream or sys.stdout
        self.poll = poll
        self.drawn = 0

    def run(self) -> int:
        """Show the job until it ends or the user presses Ctrl+C.

        Returns:
            0 after showing a job, 1 if none runs.
        """
        pid = self.control.active_pid()
        if pid is None:
            print(f"no {self.control.name} run is active - nothing to show", file=self.stream)
            return 1
        log = self.control.active_log()
        print(f"{self.control.name} (pid {pid}), log {log or '-'} - Ctrl+C leaves the view, "
              f"the run continues", file=self.stream)
        offset = self._backlog(log)
        try:
            while True:
                offset = self._follow(log, offset)
                if self.control.active_pid() != pid:
                    self._clear()
                    print(f"{self.control.name} run has ended", file=self.stream)
                    return 0
                self._draw()
                time.sleep(self.poll)
        except KeyboardInterrupt:
            self._clear()
            print(f"left the view - {self.control.name} keeps running in the background "
                  f"('mediatools stop' ends it after the current file)", file=self.stream)
            return 0

    def _backlog(self, log: str | None) -> int:
        """Print the last lines of the log.

        Args:
            log: Log file, or None.

        Returns:
            Offset after the printed part.
        """
        if not log or not os.path.isfile(log):
            return 0
        with open(log, "rb") as handle:
            data = handle.read()
        data = data[:data.rfind(b"\n") + 1]
        lines = self.terminal_lines(data.decode("utf-8", errors="replace"))
        for line in lines[-self.BACKLOG:]:
            self.stream.write(line)
        self.stream.flush()
        return len(data)

    def _follow(self, log: str | None, offset: int) -> int:
        """Print log lines written since ``offset``.

        Args:
            log: Log file, or None.
            offset: Position after the last printed line.

        Returns:
            New position.
        """
        if not log or not os.path.isfile(log):
            return offset
        with open(log, "rb") as handle:
            handle.seek(offset)
            data = handle.read()
        data = data[:data.rfind(b"\n") + 1]          # keep an unfinished line for later
        lines = self.terminal_lines(data.decode("utf-8", errors="replace"))
        if lines:
            self._clear()
            self.stream.write("".join(lines))
            self.stream.flush()
        return offset + len(data)

    @classmethod
    def terminal_lines(cls, text: str) -> list[str]:
        """Turn log file lines into what the terminal of the run shows.

        Args:
            text: Complete log lines.

        Returns:
            Lines with line break: DEBUG dropped, INFO without prefix,
            other levels as ``LEVEL message``; foreign lines unchanged.
        """
        shown = []
        for line in text.splitlines(True):
            match = cls.LOG_LINE.match(line)
            if match is None:
                shown.append(line)
            elif match.group(1) == "INFO":
                shown.append(match.group(2))
            elif match.group(1) != "DEBUG":
                shown.append(f"{match.group(1)} {match.group(2)}")
        return shown

    def _draw(self) -> None:
        """Redraw the progress lines from the status file."""
        try:
            with open(self.control.status_file, encoding="utf-8") as handle:
                lines = handle.read().splitlines()[:3]
        except FileNotFoundError:
            return
        if len(lines) < 2 or not self.stream.isatty():
            return
        width = max(20, shutil.get_terminal_size().columns - 1)
        if len(lines) < self.drawn:              # fewer lines than before: clear first
            self._clear()
        up = "\033[1A" * (self.drawn - 1) if self.drawn > 1 else ""
        self.stream.write("\r" + up + "\n".join("\033[K" + line[:width] for line in lines))
        self.stream.flush()
        self.drawn = len(lines)

    def _clear(self) -> None:
        """Remove the progress lines before new log lines are printed."""
        if self.drawn:
            self.stream.write("\r\033[K" + "\033[1A\r\033[K" * (self.drawn - 1))
            self.stream.flush()
            self.drawn = 0
