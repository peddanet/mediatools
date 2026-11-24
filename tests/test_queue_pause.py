"""A stopped or aborted queue job stays in the queue, paused; ``stop --now`` aborts."""
import argparse
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from mediatools.cli import Cli
from mediatools.core.runcontrol import RunControl
from mediatools.core.runlog import RunLog
from mediatools.gui.shrinkmonitor import ShrinkMonitor
from mediatools.shrink.queuecommand import QueueCommand
from mediatools.shrink.shrinkqueue import ShrinkQueue

DEAD_PID = 2 ** 22 + 1            # above the kernel's pid_max default: never alive


def started(queue: ShrinkQueue, pid: int | None = None) -> dict:
    """Take the first job off the queue, as ``queue --run`` does.

    Args:
        queue: The queue.
        pid: Process ID to record instead of this process (a crashed job).

    Returns:
        The started entry.
    """
    entry = queue.pop()
    assert entry is not None
    if pid is not None:
        record = json.loads(Path(queue.current_file).read_text(encoding="utf-8"))
        record["pid"] = pid
        Path(queue.current_file).write_text(json.dumps(record), encoding="utf-8")
    return entry


def test_pop_records_the_job_with_its_pid(tmp_path: Path) -> None:
    """The started job is remembered (mode 600) until it ends.

    Args:
        tmp_path: Temporary directory.
    """
    queue = ShrinkQueue(str(tmp_path / "q.json"))
    queue.add(["/a"], "note a")
    started(queue)
    record = json.loads(Path(queue.current_file).read_text(encoding="utf-8"))
    assert record["args"] == ["/a"] and record["pid"] == os.getpid()
    assert oct(os.stat(queue.current_file).st_mode & 0o777) == "0o600"
    assert queue.take_current() == record and not os.path.exists(queue.current_file)
    assert queue.take_current() is None


def test_normal_end_forgets_the_job(tmp_path: Path) -> None:
    """A job that ends normally does not come back.

    Args:
        tmp_path: Temporary directory.
    """
    queue = ShrinkQueue(str(tmp_path / "q.json"))
    queue.add(["/a"])
    started(queue)
    assert queue.continue_after(0, False, RunLog("queue-test")) == 0
    assert queue.entries() == [] and not os.path.exists(queue.current_file)


@pytest.mark.parametrize("code", [1, 2])
def test_stopped_job_goes_back_first_paused(tmp_path: Path, code: int) -> None:
    """Stopped (or aborted) by the user: first entry, marked paused, queue ends.

    Args:
        tmp_path: Temporary directory.
        code: Exit code of the stopped job (a stop may also end with 2).
    """
    queue = ShrinkQueue(str(tmp_path / "q.json"), python="/no/python")
    queue.add(["/a"], "note a")
    queue.add(["/b"])
    started(queue)
    assert queue.continue_after(code, False, RunLog("queue-test"), stopped=True) == code
    entries = queue.entries()
    assert [e["args"] for e in entries] == [["/a"], ["/b"]]
    assert entries[0]["paused"] and entries[0]["note"] == "note a" and "pid" not in entries[0]
    assert queue.lines()[0].startswith("1. mediatools shrink /a   (paused ")


def test_error_end_also_keeps_the_job(tmp_path: Path) -> None:
    """A job ended by an error is not lost either.

    Args:
        tmp_path: Temporary directory.
    """
    queue = ShrinkQueue(str(tmp_path / "q.json"))
    queue.add(["/a"])
    started(queue)
    assert queue.continue_after(1, False, RunLog("queue-test")) == 1
    assert queue.entries()[0]["args"] == ["/a"] and queue.entries()[0]["paused"]


def test_stop_then_next_puts_the_job_behind_the_next(tmp_path: Path) -> None:
    """*Stop, then next*: the next job starts, the stopped one waits behind it.

    Args:
        tmp_path: Temporary directory.
    """
    queue = ShrinkQueue(str(tmp_path / "q.json"), python="/no/python")
    queue.add(["/a"])
    queue.add(["/b"])
    queue.add(["/c"])
    started(queue)
    queue.next_after_stop()
    assert queue.continue_after(1, False, RunLog("queue-test"), stopped=True) == 1
    # /b was taken to start (the interpreter is missing, so it is put back first)
    assert [e["args"][0] for e in queue.entries()] == ["/b", "/a", "/c"]
    assert queue.entries()[1]["paused"] and not os.path.exists(queue.current_file)


def test_stop_then_next_with_nothing_next_does_not_restart(tmp_path: Path) -> None:
    """Without another job the stopped one stays paused instead of starting again.

    Args:
        tmp_path: Temporary directory.
    """
    queue = ShrinkQueue(str(tmp_path / "q.json"), python="/no/python")
    queue.add(["/a"])
    started(queue)
    queue.next_after_stop()
    assert queue.continue_after(1, False, RunLog("queue-test"), stopped=True) == 1
    assert [e["args"] for e in queue.entries()] == [["/a"]] and queue.entries()[0]["paused"]


def test_recover_only_a_dead_record(tmp_path: Path) -> None:
    """A record of a living process stays; one left by a crash comes back.

    Args:
        tmp_path: Temporary directory.
    """
    queue = ShrinkQueue(str(tmp_path / "q.json"))
    assert not queue.recover()                         # no record
    queue.add(["/a"])
    started(queue)                                     # this process: alive
    assert not queue.recover() and queue.entries() == []
    queue.add(["/b"])
    started(queue, pid=DEAD_PID)
    assert queue.recover() and queue.entries()[0]["args"] == ["/b"]
    Path(queue.current_file).write_text("not json", encoding="utf-8")
    assert not queue.recover() and not os.path.exists(queue.current_file)
    assert ShrinkQueue._alive("x") is False            # pylint: disable=protected-access


def test_queue_command_recovers_and_run_three_keeps_order(tmp_path: Path,
                                                          capsys: pytest.CaptureFixture) -> None:
    """``queue`` puts a crashed job back; a refused start leaves no record.

    Args:
        tmp_path: Temporary directory.
        capsys: Output capture.
    """
    queue = ShrinkQueue(str(tmp_path / "q.json"))
    queue.add(["/a"])
    started(queue, pid=DEAD_PID)
    base = {"add": None, "note": "", "first": False, "remove": None, "move": None,
            "to": None, "clear": False, "run": False}
    command = QueueCommand(queue, lambda _a: None, lambda _a: 3)
    assert command.run(argparse.Namespace(**base)) == 0
    assert "back as first entry (paused)" in capsys.readouterr().out
    assert command.run(argparse.Namespace(**{**base, "run": True})) == 3
    assert queue.entries()[0]["args"] == ["/a"] and not os.path.exists(queue.current_file)


def test_request_abort_sends_sigterm(datadir: Path) -> None:
    """``stop --now`` signals the job; nothing runs: nothing to abort.

    Args:
        datadir: Data directory (fixture).
    """
    control = RunControl("shrink")
    assert control.request_abort() is None
    assert ShrinkMonitor(control).abort().startswith("no shrink run is active")
    with subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"]) as child:
        try:
            os.makedirs(os.path.dirname(control.pid_file), exist_ok=True)
            Path(control.pid_file).write_text(f"{child.pid}\n", encoding="utf-8")
            message = ShrinkMonitor(control).abort()
            assert message.startswith(f"abort requested: shrink (pid {child.pid})")
            assert child.wait(10) == -signal.SIGTERM
        finally:
            child.kill()
            child.wait(10)
    assert datadir.exists()


def test_cli_stop_now(datadir: Path, capsys: pytest.CaptureFixture) -> None:
    """``mediatools stop --now`` without a run says so and withdraws the marker.

    Args:
        datadir: Data directory (fixture).
        capsys: Output capture.
    """
    ShrinkQueue.default().next_after_stop()
    assert Cli().main(["stop", "--now"]) == 1
    assert "nothing to abort" in capsys.readouterr().out
    assert not os.path.exists(ShrinkQueue.default().next_file)
    assert datadir.exists()
