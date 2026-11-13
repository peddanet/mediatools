"""Release 1.6.0: jobs, ledger, filters, parallel encodes, job selection."""
import io
import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from conftest import make_file
from test_shrink_run import FakeEncoder, FakeProbe
from mediatools.cli import Cli
from mediatools.core.progressbar import ProgressBar
from mediatools.core.progressbase import ProgressBase
from mediatools.shrink import ShrinkDatabase, Shrinker, ShrinkOptions, ThroughputLog
from mediatools.shrink.fileresult import FileResult
from mediatools.shrink.jobchooser import JobChooser
from mediatools.shrink.jobregistry import JobEntry, JobRegistry
from mediatools.shrink.jobspec import JobSpec
from mediatools.shrink.ledger import Ledger
from mediatools.shrink.ledgerrow import LedgerRow
from mediatools.shrink.logbackfill import LogBackfill
from mediatools.shrink.resulttable import ResultTable
from mediatools.shrink.spacetally import SpaceTally

LOG = logging.getLogger("test160")


def row(path: str, status: str = "shrunk", source: int = 1000, result: int | None = 200,
        session: str = "shrink_2026-11-13_10-00-00", **extra) -> LedgerRow:
    """Build a ledger entry.

    Args:
        path: Absolute path.
        status: Status.
        source: Source bytes.
        result: Output bytes.
        session: Session name.
        **extra: ``length``, ``mtime`` or ``when``.

    Returns:
        The entry.
    """
    return LedgerRow(extra.get("when", "2026-11-13 10:00:00"), session, status, source, result,
                     60.0, extra.get("length"), extra.get("mtime"), path)


# -- job definition -----------------------------------------------------------
def test_sizes_dates_and_filters() -> None:
    """Sizes are decimal, dates local midnight; filters round-trip as options."""
    assert JobSpec.parse_size("300M") == 300_000_000
    assert JobSpec.parse_size("1.5G") == 1_500_000_000 and JobSpec.parse_size("42") == 42
    with pytest.raises(ValueError):
        JobSpec.parse_size("big")
    with pytest.raises(ValueError):
        JobSpec.parse_date("13.11.2026")
    spec = JobSpec("/m/c", frozenset({".mp4", ".mkv"}), 60.0, 300_000_000,
                   JobSpec.parse_date("2026-11-01"))
    assert spec.filters() == ["--ext", "mkv,mp4", "--min-seconds", "60", "--min-size", "300M",
                              "--newer-than", "2026-11-01"]
    assert JobSpec.from_dict(spec.as_dict()) == spec and len(spec.key) == 12
    assert JobSpec("/m/c").label() == "/m/c" and JobSpec("/m/c").key != spec.key


def test_job_membership() -> None:
    """A finished file counts for every job whose root and filters contain it."""
    spec = JobSpec("/m/c/a", min_size=500, min_seconds=60.0)
    assert spec.matches(row("/m/c/a/x/clip.mp4", length=120.0))
    assert spec.matches(row("/m/c/a/clip.mp4"))                # unknown length passes
    assert not spec.matches(row("/m/c/ab/clip.mp4"))           # sibling, not below
    assert not spec.matches(row("/m/c/a/clip.mkv"))            # extension
    assert not spec.matches(row("/m/c/a/clip.mp4", source=100))
    assert not spec.matches(row("/m/c/a/clip.mp4", length=30.0))
    dated = JobSpec("/m/c", newer_than=1000.0)
    assert dated.matches(row("/m/c/v.mp4", mtime=2000.0))
    assert not dated.matches(row("/m/c/v.mp4"))                # unknown age fails a date filter


# -- ledger ---------------------------------------------------------------------
def test_ledger_row_round_trip() -> None:
    """Optional fields survive; a tab in a name cannot break the columns."""
    item = row("/m/c/a\tb.mp4", result=None, length=12.5, mtime=1700000000.0)
    parsed = LedgerRow.parse(item.line())
    assert parsed is not None and parsed.result is None and parsed.length == 12.5
    assert parsed.path == "/m/c/a b.mp4" and parsed.mtime == 1700000000.0
    assert LedgerRow.parse("# comment") is None and LedgerRow.parse("a\tb") is None


def write_log(folder: Path, name: str, root: str, items: list[FileResult],
              width: int = 80) -> Path:
    """Write a shrink log the way a run does (formatter of RunLog).

    Args:
        folder: Log folder.
        name: Log file name.
        root: Shrink root.
        items: Results written as table rows.
        width: Width of the table (narrow: paths wrap).

    Returns:
        The log path.
    """
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    logger = logging.getLogger("backfill-" + name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
    logger.addHandler(handler)
    logger.info("shrink root: %s  mode: replace originals  order: largest", root)
    table = ResultTable(root, str(folder / "report.txt"), logger, width)
    for item in items:
        table.add(item)
    table.finish()
    logger.removeHandler(handler)
    handler.close()
    return path


def test_backfill_reads_wrapped_paths_and_figures(tmp_path: Path) -> None:
    """Old logs give path, sizes, length and status; the ledger is created once.

    Args:
        tmp_path: Temporary directory.
    """
    long_name = "a much longer folder name/another folder/and the clip name.mp4"
    write_log(tmp_path / "logs", "shrink_2026-11-09_08-00-00.log", "/m/c", [
        FileResult("/m/c/" + long_name, "shrunk", 2_860_000_000, 510_000_000, 2658.0, 1674.0),
        FileResult("/m/c/x/failed.mp4", "FAILED", 100_000_000, None, None, 30.0),
        FileResult("/m/c/x/old.mp4", "HEVC", 5_000_000)])
    rows = LogBackfill(str(tmp_path / "logs")).rows()
    assert [item.status for item in rows] == ["shrunk", "FAILED", "HEVC"]
    assert rows[0].path == "/m/c/" + long_name and rows[0].source == 2_860_000_000
    assert rows[0].result == 510_000_000 and rows[0].length == pytest.approx(2658.0, abs=3)
    assert rows[0].session == "shrink_2026-11-09_08-00-00" and rows[0].mtime is None
    ledger = Ledger(str(tmp_path / "data/ledger.tsv"), str(tmp_path / "logs"))
    assert ledger.ensure() == 3 and ledger.ensure() == 0
    assert oct(os.stat(ledger.path).st_mode & 0o777) == "0o600"
    sums = ledger.sums()
    assert sums.read == 2_960_000_000 and sums.files == 2 and sums.since == rows[0].when[:10]
    assert sums.freed_src - sums.freed_out == 2_350_000_000
    assert ledger.sums(JobSpec("/m/c/x")).files == 1


def test_space_tally_from_ledger(tmp_path: Path) -> None:
    """Session and total come from the ledger, labelled with the first date.

    Args:
        tmp_path: Temporary directory.
    """
    ledger = Ledger(str(tmp_path / "ledger.tsv"))
    ledger.add(row("/m/a.mp4", when="2026-11-09 10:00:00", session="shrink_2026-11-09_09-00-00"))
    ledger.add(row("/m/b.mp4", source=3000, result=1000))
    tally = SpaceTally.from_ledger(ledger, "shrink_2026-11-13_10-00-00")
    assert tally.session() == (3000, 1000) and tally.overall() == (4000, 1200)
    assert tally.since == "total since 2026-11-09" and tally.started == "2026-11-13 10:00"


# -- registry and selection -----------------------------------------------------
def test_registry_progress_and_arguments(tmp_path: Path) -> None:
    """A planned job shows done (ledger) and to go (plan minus finished files).

    Args:
        tmp_path: Temporary directory.
    """
    ledger = Ledger(str(tmp_path / "ledger.tsv"))
    ledger.add(row("/m/c/done.mp4", source=3000))
    registry = JobRegistry(str(tmp_path / "jobs.json"), ledger)
    spec = JobSpec("/m/c", min_size=300_000_000)
    registry.planned(spec, {"crf": 26, "order": "largest", "jobs": 2}, 9000, 3)
    registry.finished_file(spec, 1000)
    entry = registry.entries()[0]
    assert (entry.todo_bytes, entry.todo_files) == (8000, 2)
    assert entry.read == 0                     # 3000 bytes are below --min-size 300M
    assert entry.argv() == ["shrink", "/m/c", "--crf", "26", "--jobs", "2",
                            "--min-size", "300M"]
    assert "crf 26" in JobRegistry.line(entry) and "/m/c  --min-size 300M" in entry.spec.label()
    whole = JobEntry(JobSpec("/m"), read=1, todo_bytes=3)
    assert whole.fraction == 0.25 and whole.gauge(4) == "█░░░"


def test_job_chooser(tmp_path: Path) -> None:
    """Number continues, n starts new, q ends; no jobs: no question.

    Args:
        tmp_path: Temporary directory.
    """
    registry = JobRegistry(str(tmp_path / "jobs.json"))
    assert JobChooser(registry, ask=lambda _q: pytest.fail("asked")).choose() == []
    registry.planned(JobSpec("/m/c"), {"crf": 28}, 10, 1)
    out: list[str] = []
    answers = iter(["7", "1"])
    assert JobChooser(registry, ask=lambda _q: next(answers), say=out.append).choose() == \
        ["shrink", "/m/c"]
    assert any("please answer" in line for line in out)
    assert JobChooser(registry, ask=lambda _q: "n", say=out.append).choose() == []
    assert JobChooser(registry, ask=lambda _q: "q", say=out.append).choose() is None


def test_plain_shrink_offers_the_jobs(tmp_path: Path, datadir: Path,
                                      monkeypatch: pytest.MonkeyPatch) -> None:
    """Only a bare ``shrink`` on a terminal asks; the choice becomes the argv.

    Args:
        tmp_path: Temporary directory.
        datadir: Data directory (fixture).
        monkeypatch: Pytest monkeypatch.
    """
    del datadir
    cli = Cli()
    Cli.registry().planned(JobSpec(str(tmp_path)), {"crf": 24}, 10, 1)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _q: "1")
    cli.argv = ["shrink"]
    args = cli._choose_job(cli.parser.parse_args(["shrink"]))  # pylint: disable=protected-access
    assert args is not None and args.crf == 24 and args.path == str(tmp_path)
    assert cli.argv == ["shrink", str(tmp_path), "--crf", "24"]
    cli.argv = ["shrink", "--crf", "30"]
    given = cli.parser.parse_args(cli.argv)
    assert cli._choose_job(given) is given                      # pylint: disable=protected-access
    with pytest.raises(SystemExit):
        cli.parser.parse_args(["shrink", "--min-size", "huge"])


# -- filters and display --------------------------------------------------------
def build(tmp_path: Path, opts: ShrinkOptions,
          encoder: FakeEncoder | None = None) -> tuple[Shrinker, FakeEncoder]:
    """Create a Shrinker on ``tmp_path/c`` with a ledger and a registry.

    Args:
        tmp_path: Temporary directory.
        opts: Run settings.
        encoder: Encoder double; a FakeEncoder if None.

    Returns:
        Shrinker and encoder.
    """
    encoder = encoder or FakeEncoder()
    shrinker = Shrinker(str(tmp_path / "c"), LOG, opts, encoder, FakeProbe(),
                        throughput=ThroughputLog(str(tmp_path / "speed.json")),
                        display=ProgressBar(1, 1, stream=io.StringIO()))
    ledger = Ledger(str(tmp_path / "ledger.tsv"))
    spec = JobSpec(shrinker.root, opts.extensions, opts.min_seconds, opts.min_size,
                   opts.newer_than)
    shrinker.state.update({"ledger": ledger, "job": spec, "session": "shrink_x",
                           "registry": JobRegistry(str(tmp_path / "jobs.json"), ledger)})
    if hasattr(encoder, "shrinker"):
        encoder.shrinker = shrinker
    return shrinker, encoder


def test_size_and_date_filters(tmp_path: Path) -> None:
    """Small and old videos are left out and counted; the rest is recorded.

    Args:
        tmp_path: Temporary directory.
    """
    make_file(tmp_path / "c/small.mp4", b"s" * 10)
    old = make_file(tmp_path / "c/old.mp4", b"o" * 500)
    make_file(tmp_path / "c/new.mp4", b"n" * 500)
    os.utime(old, (1_000_000, 1_000_000))
    opts = ShrinkOptions(min_size=100, newer_than=2_000_000.0)
    shrinker, encoder = build(tmp_path, opts)
    assert shrinker.run() == 0 and encoder.calls == ["new.mp4"]
    assert shrinker.stats["small"] == 1 and shrinker.stats["older"] == 1
    rows = shrinker.state["ledger"].rows()
    assert [item.path for item in rows] == [str(tmp_path / "c/new.mp4")]
    assert rows[0].mtime is not None and rows[0].status == "shrunk"
    entry = shrinker.state["registry"].entries()[0]
    assert entry.todo_files == 0 and entry.read == 500 and entry.settings["jobs"] == 1


def test_progress_line_counts_the_job_and_several_files() -> None:
    """Line 1 is the job (base + this run); one file line per running encode."""
    stream = io.StringIO()
    stream.isatty = lambda: True                                # type: ignore[method-assign]
    display = ProgressBar(1000, 4, 100.0, stream=stream)
    display.base = ProgressBase(1000, 2, "2026-11-09")
    display.show_many([("a.mp4", 0.5, 400), ("b.mp4", 0.25, 400)], "space x")
    text = stream.getvalue()
    assert "job   [" in text and "since 2026-11-09" in text and "65.0 %" in text
    assert text.count("file ") == 2 and "space x" in text and display.drawn == 4


# -- parallel encodes -----------------------------------------------------------
class BarrierEncoder(FakeEncoder):
    """Encoder double that succeeds only if two encodes run at the same time."""

    def __init__(self, parties: int = 2) -> None:
        """Create the shared barrier.

        Args:
            parties: Encodes that must meet.
        """
        super().__init__()
        self.barrier = threading.Barrier(parties, timeout=5)
        self.lock = threading.Lock()
        self.active: set[int] = set()
        self.overlap: list[set[int]] = []

    def encode(self, src: str, dst: str,
               on_progress: Callable[[float], None] | None = None) -> bool:
        """Meet the other slot (first round only), then write the output.

        Args:
            src: Input.
            dst: Output.
            on_progress: Progress callback.

        Returns:
            True.
        """
        inode = os.stat(src).st_ino
        with self.lock:
            self.overlap.append(set(self.active) & {inode})
            self.active.add(inode)
        try:
            if len(self.calls) < self.barrier.parties and not self.barrier.broken:
                try:
                    self.barrier.wait()
                except threading.BrokenBarrierError:
                    pass
            time.sleep(0.05)
            return super().encode(src, dst, on_progress)
        finally:
            with self.lock:
                self.active.discard(inode)


def test_two_encodes_run_at_the_same_time(tmp_path: Path) -> None:
    """With jobs=2 two encodes meet; every file is replaced and recorded once.

    Args:
        tmp_path: Temporary directory.
    """
    for name in ("a", "b", "c"):
        make_file(tmp_path / f"c/{name}.mp4", name.encode() * 100)
    encoder = BarrierEncoder()
    shrinker, _ = build(tmp_path, ShrinkOptions(jobs=2), encoder)
    started = time.monotonic()
    assert shrinker.run() == 0 and time.monotonic() - started < 4
    assert not encoder.barrier.broken and sorted(encoder.calls) == ["a.mp4", "b.mp4", "c.mp4"]
    assert shrinker.stats["shrunk"] == 3 and len(shrinker.state["encoders"]) == 2
    assert (tmp_path / "c/a.mp4").read_bytes() == b"shrunk:a.mp4"
    assert not (tmp_path / "c/newShrinkedFiles").exists()
    assert ShrinkDatabase(str(tmp_path / "c"), LOG).is_done(str(tmp_path / "c/b.mp4"))
    assert len(shrinker.state["ledger"].rows()) == 3


def test_hard_links_never_encode_at_the_same_time(tmp_path: Path) -> None:
    """Two names of one inode wait for each other.

    Args:
        tmp_path: Temporary directory.
    """
    first = make_file(tmp_path / "c/x/one.mp4", b"1" * 300)
    (tmp_path / "c/y").mkdir()
    os.link(first, tmp_path / "c/y/one-link.mp4")
    make_file(tmp_path / "c/z/other.mp4", b"2" * 200)
    encoder = BarrierEncoder()
    shrinker, _ = build(tmp_path, ShrinkOptions(jobs=2), encoder)
    shrinker.run()
    assert not any(encoder.overlap)


def test_stop_waits_for_running_encodes_and_starts_none(tmp_path: Path) -> None:
    """A stop during parallel encodes lets them finish and leaves the rest.

    Args:
        tmp_path: Temporary directory.
    """
    for name in ("a", "b", "c", "d"):
        make_file(tmp_path / f"c/{name}.mp4", name.encode() * (100 + ord(name)))
    shrinker, encoder = build(tmp_path, ShrinkOptions(jobs=2), BarrierEncoder())
    original = encoder.encode

    def stop_then_encode(src: str, dst: str,
                         on_progress: Callable[[float], None] | None = None) -> bool:
        """Encode, then request a stop.

        Args:
            src: Input.
            dst: Output.
            on_progress: Progress callback.

        Returns:
            Result of the encode.
        """
        result = original(src, dst, on_progress)     # both slots met at the barrier
        shrinker.flags["stop"] = True
        return result

    encoder.encode = stop_then_encode                           # type: ignore[method-assign]
    assert shrinker.run() == 1
    assert len(encoder.calls) == 2 and shrinker.stats["shrunk"] == 2


def test_abort_reaches_every_slot(tmp_path: Path) -> None:
    """The second Ctrl+C aborts the encoders of all slots.

    Args:
        tmp_path: Temporary directory.
    """
    shrinker, encoder = build(tmp_path, ShrinkOptions(jobs=3))
    shrinker.state["encoders"] = shrinker._slots()              # pylint: disable=protected-access
    shrinker.flags["stop"] = True
    shrinker.on_signal(2)
    assert all(slot.aborted for slot in shrinker.state["encoders"]) and encoder.aborted
    with pytest.raises(ValueError):
        ShrinkOptions(jobs=0)
