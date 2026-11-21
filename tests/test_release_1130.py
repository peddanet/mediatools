"""Release 1.13.0: shrink queue, proposed jobs, remainders of every matching job."""
import argparse
import json
import os
import sys
from pathlib import Path

import pytest

from conftest import make_file, make_video
from test_release_190 import MB, row
from mediatools.cli import Cli
from mediatools.core.datadir import DataDir
from mediatools.core.runlog import RunLog
from mediatools.core.selfrestart import SelfRestart
from mediatools.shrink.jobregistry import JobRegistry
from mediatools.shrink.jobspec import JobSpec
from mediatools.shrink.jobsuggester import JobSuggester, SuggestedJob
from mediatools.shrink.ledger import Ledger
from mediatools.shrink.ledgerrow import LedgerRow
from mediatools.shrink.potential import ShrinkPotential
from mediatools.shrink.queuecommand import QueueCommand
from mediatools.shrink.shrinker import Shrinker
from mediatools.shrink.shrinkqueue import ShrinkQueue
from mediatools.shrink.throughput import ThroughputLog


def queue_args(**given: object) -> argparse.Namespace:
    """Build the arguments of ``mediatools queue``.

    Args:
        **given: Options to set.

    Returns:
        Namespace with every option of the command.
    """
    base: dict[str, object] = {"add": None, "note": "", "first": False, "remove": None,
                               "move": None, "to": None, "clear": False, "run": False}
    base.update(given)
    return argparse.Namespace(**base)


# -- the queue file --------------------------------------------------------------------
def test_queue_add_move_remove_pop(tmp_path: Path) -> None:
    """The list keeps its order; positions start at 1; the file has mode 600.

    Args:
        tmp_path: Temporary directory.
    """
    queue = ShrinkQueue(str(tmp_path / "q.json"), python="/py")
    assert queue.entries() == [] and queue.lines() == ["queue: empty"] and queue.summary() == []
    assert queue.add(["/a"], "first") == 1 and queue.add(["/b"]) == 2
    assert queue.add(["/c", "--jobs", "2"], front=True) == 1
    assert oct(os.stat(tmp_path / "q.json").st_mode & 0o777) == "0o600"
    assert [e["args"][0] for e in queue.entries()] == ["/c", "/a", "/b"]
    queue.move(1, 9)
    assert [e["args"][0] for e in queue.entries()] == ["/a", "/b", "/c"]
    assert queue.lines()[0] == "1. mediatools shrink /a   (first)"
    assert "3 job(s) waiting" in queue.summary()[0]
    assert queue.remove(2)["args"] == ["/b"]
    with pytest.raises(ValueError):
        queue.remove(5)
    with pytest.raises(ValueError):
        queue.move(0, 1)
    assert queue.pop() is not None and queue.clear() == 1 and queue.pop() is None
    assert queue.command(["/x"]) == ["/py", "-m", "mediatools", "shrink", "/x",
                                     "--foreground", "-y"]
    (tmp_path / "q.json").write_text("{bad", encoding="utf-8")
    assert queue.entries() == []
    (tmp_path / "q.json").write_text(json.dumps([{"args": "x"}, 3, {"args": ["/y"]}]),
                                     encoding="utf-8")
    assert queue.entries() == [{"args": ["/y"]}]
    assert ShrinkQueue.default().path.endswith(ShrinkQueue.FILE)


def test_continue_after_only_after_a_normal_end(tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop, a declined start or an empty queue end the chain; otherwise the next job runs.

    Args:
        tmp_path: Temporary directory.
        monkeypatch: Patcher.
    """
    queue = ShrinkQueue(str(tmp_path / "q.json"), python="/py")
    run = RunLog("queue-test")
    assert queue.continue_after(0, False, run) == 0              # empty
    queue.add(["/a", "--min-seconds", "120"], "n")
    assert queue.continue_after(1, False, run) == 1               # stopped: paused
    assert queue.continue_after(0, True, run) == 0                # declined
    assert len(queue.entries()) == 1
    started: list[list[str]] = []

    def fake_exec(program: str, argv: list[str]) -> None:
        """Record the start and fail like a missing program.

        Args:
            program: Program.
            argv: Arguments.

        Raises:
            OSError: Always.
        """
        started.append([program, *argv[1:]])
        raise OSError("gone")

    monkeypatch.setattr(os, "execv", fake_exec)
    assert queue.continue_after(2, False, RunLog("queue-test")) == 1
    assert started == [["/py", "-m", "mediatools", "shrink", "/a", "--min-seconds", "120",
                        "--foreground", "-y"]]
    assert queue.entries()[0]["args"][0] == "/a"                  # put back first


# -- the command -----------------------------------------------------------------------
def test_queue_command_changes_and_lists(tmp_path: Path,
                                         capsys: pytest.CaptureFixture[str]) -> None:
    """--add is checked with the shrink parser; changes print the list.

    Args:
        tmp_path: Temporary directory.
        capsys: Output capture.
    """
    folder = tmp_path / "v"
    folder.mkdir()
    cli = Cli()
    queue = ShrinkQueue(str(tmp_path / "q.json"))
    command = QueueCommand(queue, lambda a: cli.parser.parse_args(["shrink", *a]),
                           lambda a: 0)
    assert command.run(queue_args(add=f"{folder} --min-seconds 120", note="n")) == 0
    assert queue.entries()[0]["args"] == [str(folder), "--min-seconds", "120"]
    assert command.run(queue_args(add=f"{folder} --jobs 2", first=True)) == 0
    assert queue.entries()[0]["args"][1:] == ["--jobs", "2"]
    for bad in (f"{folder} --nonsense", "--jobs 2", str(tmp_path / "none"), f"'{folder}",
                ""):
        assert command.run(queue_args(add=bad or " ")) == 2
    assert command.run(queue_args(move=1)) == 2
    assert command.run(queue_args(move=2, to=1)) == 0
    assert queue.entries()[0]["args"][1:] == ["--min-seconds", "120"]
    assert command.run(queue_args(remove=1)) == 0 and len(queue.entries()) == 1
    assert command.run(queue_args(clear=True)) == 0 and queue.entries() == []
    out = capsys.readouterr().out
    assert "added as job 1" in out and "removed: mediatools shrink" in out
    assert "queue: empty" in out and "Error: --move needs --to" in out


def test_queue_run_starts_first_or_puts_it_back(tmp_path: Path,
                                                capsys: pytest.CaptureFixture[str]) -> None:
    """--run starts the first job; while another shrink runs it stays first.

    Args:
        tmp_path: Temporary directory.
        capsys: Output capture.
    """
    queue = ShrinkQueue(str(tmp_path / "q.json"))
    seen: list[list[str]] = []
    answers = [3, 0]

    def start(args: list[str]) -> int:
        """Record and answer.

        Args:
            args: Shrink arguments.

        Returns:
            The next prepared exit code.
        """
        seen.append(args)
        return answers.pop(0)

    command = QueueCommand(queue, lambda a: None, start)
    assert command.run(queue_args(run=True)) == 0
    queue.add(["/a"], "x")
    assert command.run(queue_args(run=True)) == 3 and queue.entries()[0]["args"] == ["/a"]
    assert command.run(queue_args(run=True)) == 0 and queue.entries() == []
    assert seen == [["/a", "-y"], ["/a", "-y"]]
    out = capsys.readouterr().out
    assert "nothing to start" in out and "stays first" in out


def test_cli_queue_and_status(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The subcommand works through the main parser; status names waiting jobs.

    Args:
        tmp_path: Temporary directory.
        capsys: Output capture.
    """
    assert Cli().main(["queue", "--add", f"{tmp_path} --min-seconds 60"]) == 0
    Cli().main(["status"])
    out = capsys.readouterr().out
    assert "added as job 1" in out and "queue:     1 job(s) waiting" in out


# -- proposed jobs ---------------------------------------------------------------------
def potential_with_levels() -> ShrinkPotential:
    """Build an estimate with groups in two levels.

    Returns:
        The estimate (fixed time 0, 1 wall second per second of playing time).
    """
    potential = ShrinkPotential([], None, 0.0)
    for _ in range(20):
        potential.add(500 * MB, 200.0, "L1")        # 2-5 min, high bit rate: best
    for _ in range(10):
        potential.add(100 * MB, 900.0, "L2")        # 10-30 min, low bit rate
    for _ in range(4):
        potential.add(300 * MB, 40.0, "L2")         # < 1 min, high bit rate
    potential.add(10 * MB, None, "L1")              # unknown length
    return potential


def test_suggester_orders_jobs_greedily() -> None:
    """The best combination first, groups are used once, tiny jobs are left out."""
    jobs = JobSuggester(potential_with_levels()).suggest(10)
    assert (jobs[0].level, jobs[0].min_seconds) == (1, 120) and jobs[0].files == 20
    assert all(job.saved >= JobSuggester.MIN_BYTES for job in jobs)
    assert sum(job.files for job in jobs) <= 35
    assert jobs[1].per_hour <= jobs[0].per_hour
    assert JobSuggester(potential_with_levels()).suggest(1) == jobs[:1]
    assert not JobSuggester(ShrinkPotential([], None, 0.0)).suggest(3)
    assert SuggestedJob(1, 0).per_hour == 0.0


def test_estimate_suggests_and_queues(tmp_path: Path,
                                      capsys: pytest.CaptureFixture[str]) -> None:
    """--suggest lists jobs; --queue stores them with the settings of the last job.

    Args:
        tmp_path: Temporary directory.
        capsys: Output capture.
    """
    outer = tmp_path / "outer"
    for number in range(3):
        make_video(outer / "in" / f"v{number}.mp4", seconds=3)
    ledger = Ledger(os.path.join(DataDir().data_dir, "ledger.tsv"))
    ledger.add(row(30 * MB, 3 * MB, 10.0, 60.0))
    ThroughputLog().add(30 * MB, 10.0, 3 * MB)
    registry = JobRegistry(os.path.join(DataDir().sub("state"), "jobs.json"))
    registry.planned(JobSpec(str(outer)), {"crf": 28, "preset": "faster", "jobs": 2,
                                           "threads": 11, "retry_failed": True}, 1, 1)
    monkey_min = JobSuggester.MIN_BYTES
    JobSuggester.MIN_BYTES = 1
    try:
        assert Cli().main(["estimate", str(outer / "in"), "--up", "1", "--suggest", "2",
                           "--queue"]) == 0
    finally:
        JobSuggester.MIN_BYTES = monkey_min
    out = capsys.readouterr().out
    assert "proposed shrink jobs" in out and "1. shrink L1 --min-seconds 0" in out
    entries = ShrinkQueue.default().entries()
    assert entries and entries[0]["args"][0] == str(outer / "in")
    assert "--preset" in entries[0]["args"] and "--retry-failed" not in entries[0]["args"]
    assert Cli().main(["estimate", str(outer / "in"), "--suggest", "1"]) == 0
    assert "no shrink job proposed" in capsys.readouterr().out
    assert Cli().main(["estimate", str(outer / "in"), "--queue"]) == 0
    assert "--queue works only with --suggest" in capsys.readouterr().out


# -- remainders of every job that includes a finished video ------------------------------
def test_finished_file_updates_every_matching_job(tmp_path: Path) -> None:
    """A video shrunk by the parent job is taken off the sub-folder job as well.

    Args:
        tmp_path: Temporary directory.
    """
    registry = JobRegistry(str(tmp_path / "jobs.json"))
    parent, sub = JobSpec(str(tmp_path / "p")), JobSpec(str(tmp_path / "p" / "s"))
    short = JobSpec(str(tmp_path / "p" / "s"), min_seconds=300.0)
    registry.planned(parent, {}, 100 * MB, 10)
    registry.planned(sub, {}, 40 * MB, 4)
    registry.planned(short, {}, 0, 0)
    inside = LedgerRow("", "", "", 10 * MB, None, 0.0, 60.0, None,
                       str(tmp_path / "p" / "s" / "a.mp4"))
    registry.finished_file(parent, 10 * MB, inside)
    data = json.loads((tmp_path / "jobs.json").read_text(encoding="utf-8"))
    assert data[parent.key]["todo_bytes"] == 90 * MB and data[sub.key]["todo_files"] == 3
    assert data[short.key]["todo_bytes"] == 0                      # short video, nothing left
    outside = LedgerRow("", "", "", 10 * MB, None, 0.0, 60.0, None, str(tmp_path / "x.mp4"))
    registry.finished_file(parent, 10 * MB, outside)
    registry.finished_file(parent, 10 * MB)
    data = json.loads((tmp_path / "jobs.json").read_text(encoding="utf-8"))
    assert data[parent.key]["todo_files"] == 7 and data[sub.key]["todo_files"] == 3
    data["broken"] = {"spec": "x", "todo_bytes": 5, "todo_files": 1}
    (tmp_path / "jobs.json").write_text(json.dumps(data), encoding="utf-8")
    registry.finished_file(JobSpec(str(tmp_path / "none")), MB, inside)


def test_shrinker_passes_the_video_to_the_registry(tmp_path: Path) -> None:
    """The job progress carries path and length, so other jobs can be updated.

    Args:
        tmp_path: Temporary directory.
    """
    calls: list[tuple] = []

    class Recorder:
        """Registry double."""

        @staticmethod
        def finished_file(spec: JobSpec, size: int, found: LedgerRow | None = None) -> None:
            """Record the call.

            Args:
                spec: Job.
                size: Size.
                found: Video.
            """
            calls.append((spec, size, found))

    video = make_file(tmp_path / "a.mp4", b"x")
    shrinker = Shrinker(str(tmp_path), RunLog("queue-test").logger)
    shrinker.state["job"], shrinker.state["registry"] = JobSpec(str(tmp_path)), Recorder()
    shrinker.planner.duration = lambda _path: 12.0             # type: ignore[method-assign]
    shrinker._job_progress(1, video)                           # pylint: disable=protected-access
    shrinker._job_progress(1)                                  # pylint: disable=protected-access
    assert calls[0][2].path == video and calls[0][2].length == 12.0 and calls[1][2] is None
    shrinker.state["registry"] = None
    shrinker._job_progress(1, video)                           # pylint: disable=protected-access
    assert len(calls) == 2


def test_switch_reports_a_failed_start(capsys: pytest.CaptureFixture[str],
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """A release switch that cannot start says so and returns 1.

    Args:
        capsys: Output capture.
        monkeypatch: Patcher.
    """
    restart = SelfRestart(["shrink", "/c"], "/log", python=sys.executable)

    def fail() -> None:
        """Fail like a missing program.

        Raises:
            OSError: Always.
        """
        raise OSError("gone")

    monkeypatch.setattr(restart, "execute", fail)
    assert restart.switch(RunLog("queue-test")) == 1
    assert "switch to the installed release failed" in capsys.readouterr().err
