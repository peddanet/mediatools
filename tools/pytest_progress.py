"""pytest_progress - test progress in the look of templates/output-demo.sh (dev-conventions bundle).

Copy to tools/pytest_progress.py and load it from tests/conftest.py, so it
works for every pytest run (check.sh, CI, IDE):

    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    pytest_plugins = ["pytest_progress"]

It prints a progress bar (percent, count, elapsed time, slowest module so
far): in a terminal one line that is redrawn, in a pipe or log a new line
at every tenth of the run. After the run it lists the five slowest tests.
Colours follow output.sh: never with NO_COLOR, else with FORCE_COLOR or a
terminal. PYTEST_PROGRESS=0 switches the plugin off.
"""

from __future__ import annotations

import os
import sys
import time
from collections import defaultdict
from typing import Any

import pytest

WIDTH = 30
SLOWEST = 5


def _colour() -> bool:
    """Tell whether colour codes are wanted (same rule as output.sh).

    Returns:
        True when colours are wanted.
    """
    if os.environ.get("NO_COLOR"):
        return False
    return bool(os.environ.get("FORCE_COLOR")) or sys.stdout.isatty()


class Progress:
    """Collects the results of the tests and draws the bar."""

    def __init__(self, terminal: Any) -> None:
        """Remember the terminal reporter of pytest.

        Args:
            terminal: The terminal reporter plugin of pytest.
        """
        self.terminal = terminal
        self.tty = sys.stdout.isatty()
        self.codes = ("\033[32m", "\033[2m", "\033[0m") if _colour() else ("", "", "")
        self.count = {"total": 0, "done": 0, "tenth": -1}
        self.start = time.monotonic()
        self.durations: list[tuple[float, str]] = []
        self.modules: dict[str, float] = defaultdict(float)

    def line(self) -> str:
        """Build the progress line.

        Returns:
            The line without line end.
        """
        green, dim, reset = self.codes
        done, total = self.count["done"], self.count["total"]
        filled = done * WIDTH // total
        slow = max(self.modules.items(), key=lambda item: item[1])[0] if self.modules else ""
        return (f"  {green}{'█' * filled}{dim}{'░' * (WIDTH - filled)}{reset}"
                f" {done * 100 // total:3d} %  {done:3d}/{total}"
                f"  {dim}{time.monotonic() - self.start:5.1f} s  {slow}{reset}")

    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(self, items: list[pytest.Item]) -> None:
        """Count the tests that will run.

        Args:
            items: The collected tests.
        """
        self.count["total"] = len(items)

    @pytest.hookimpl(hookwrapper=True)
    def pytest_report_teststatus(self) -> Any:
        """Drop the progress letters of pytest (".", "F", "s"); the bar replaces them.

        Yields:
            Control to the other implementations; their result keeps its category and word.
        """
        outcome = yield
        result = outcome.get_result()
        if result and self.terminal.verbosity <= 0:
            outcome.force_result((result[0], "", result[2]))

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        """Count a finished test and draw the bar.

        Args:
            report: Report of one phase of one test.
        """
        self.modules[report.nodeid.split("::", 1)[0]] += report.duration
        if report.when == "call":
            self.durations.append((report.duration, report.nodeid))
        if report.when != "teardown" or not self.count["total"]:
            return
        self.count["done"] += 1
        if self.tty:
            self.terminal.write("\r" + self.line())
            if self.count["done"] == self.count["total"]:
                self.terminal.write("\n")
            return
        tenth = self.count["done"] * 10 // self.count["total"]
        if tenth != self.count["tenth"]:
            self.count["tenth"] = tenth
            self.terminal.write_line(self.line())

    def pytest_terminal_summary(self) -> None:
        """List the slowest tests."""
        if not self.durations:
            return
        _, dim, reset = self.codes
        self.terminal.write_line("")
        self.terminal.write_line(f"  {dim}Slowest tests:{reset}")
        for duration, nodeid in sorted(self.durations, reverse=True)[:SLOWEST]:
            self.terminal.write_line(f"  {dim}{duration:6.2f} s  {nodeid}{reset}")


@pytest.hookimpl(trylast=True)
def pytest_configure(config: pytest.Config) -> None:
    """Register the progress bar (after the terminal reporter) unless it is switched off.

    Args:
        config: The pytest configuration.
    """
    terminal = config.pluginmanager.get_plugin("terminalreporter")
    if terminal is None or os.environ.get("PYTEST_PROGRESS") == "0":
        return
    config.pluginmanager.register(Progress(terminal), "pytest-progress-bar")
