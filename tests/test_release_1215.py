"""1.2.15: ``mediatools status`` shows the running shrink, its release and its log."""
import os
from pathlib import Path

import pytest

from mediatools import __version__
from mediatools.cli import Cli
from mediatools.core.runcontrol import RunControl
from mediatools.core.runstatus import RunStatus

HEAD = "2026-11-10 03:21:47,993 INFO    log file: x\n"


def register(tmp_path: Path, log_text: str | None) -> RunControl:
    """Register this test process as running shrink with a log.

    Args:
        tmp_path: Temporary directory.
        log_text: Content of the log, or None for no log file.

    Returns:
        The run control of ``shrink``.
    """
    log = tmp_path / "shrink.log"
    if log_text is not None:
        log.write_text(log_text, encoding="utf-8")
    control = RunControl("shrink", log_path=str(log))
    control.start()
    return control


@pytest.mark.parametrize(("running", "installed", "advice"), [
    ("1.2.15", "1.2.15", "up to date"),
    ("1.2.12", "1.2.15", "'mediatools reload' switches"),
    ("1.2.7", "1.2.15", "1.2.7 cannot switch releases"),
    ("1.3.0", "1.2.15", "newer than the installed"),
    (None, "1.2.15", "unknown"),
])
def test_advice(running: str | None, installed: str, advice: str) -> None:
    """The advice depends on both versions; reload exists from 1.2.10 on.

    Args:
        running: Version of the process.
        installed: Installed version.
        advice: Expected start or part of the advice.
    """
    assert advice in RunStatus(RunControl("shrink"), installed).advice(running)


def test_last_version_line_counts(tmp_path: Path) -> None:
    """After a release switch the appended version line is the current one.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    control = register(tmp_path, HEAD
                       + "2026-11-10 03:21:47,994 INFO    mediatools 1.2.12 (pid 459449)\n"
                       + "2026-11-10 05:10:02,001 INFO    mediatools 1.2.15 (pid 459729)\n")
    try:
        lines = RunStatus(control, "1.2.15").lines()
    finally:
        control.finish()
    assert lines[0] == f"shrink: running (pid {os.getpid()})"
    assert lines[1] == "running:   1.2.15  (started 2026-11-10 05:10:02)"
    assert lines[3] == "status:    up to date"
    assert lines[4] == f"log:       {tmp_path / 'shrink.log'}"


def test_pending_requests_and_missing_log(tmp_path: Path) -> None:
    """Requested reload and stop are listed; a missing log gives an unknown version.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    control = register(tmp_path, None)
    try:
        control.request_restart()
        control.request_stop()
        lines = RunStatus(control, "1.2.15").lines()
    finally:
        control.finish()
    assert lines[1] == "running:   ?"
    assert "unknown" in lines[3]
    assert any("reload requested" in line for line in lines)
    assert any("stop requested" in line for line in lines)


def test_cli_status(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """``mediatools status``: exit 0 with a running shrink, 1 without.

    Args:
        tmp_path: Temporary directory (fixture).
        capsys: Output capture (fixture).
    """
    assert Cli().main(["status"]) == 1
    assert capsys.readouterr().out.splitlines() == ["shrink: not running",
                                                    f"installed: {__version__}"]
    control = register(tmp_path, HEAD + "2026-11-10 03:21:47,994 INFO    mediatools 1.2.7 "
                                        "(pid 1)\n")
    try:
        assert Cli().main(["status"]) == 0
    finally:
        control.finish()
    assert "1.2.7 cannot switch releases" in capsys.readouterr().out


def test_version_line_of_a_source_checkout(tmp_path: Path) -> None:
    """A run from a clone logs its path after the version; the version is still found.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    log = tmp_path / "x.log"
    log.write_text("2026-11-10 06:00:00,000 INFO    mediatools 1.2.15 (source checkout "
                   "/home/u/Projects/mediatools, not an installed release) (pid 7)\n"
                   "2026-11-10 06:00:01,000 INFO    mediatools: unrelated line\n",
                   encoding="utf-8")
    assert RunStatus(RunControl("shrink")).running_version(str(log)) == \
        ("1.2.15", "2026-11-10 06:00:00")
