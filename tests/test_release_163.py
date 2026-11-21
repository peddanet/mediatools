"""Release 1.6.3: progress of every parallel slot and file ETA (update resume: 1.14.0)."""
import io
import os
import time
from collections.abc import Callable
from concurrent.futures import Future
from pathlib import Path

from conftest import make_file
from test_release_160 import BarrierEncoder, build
from mediatools.core.progressbar import ProgressBar
from mediatools.core.datadir import DataDir
from mediatools.core.runcontrol import RunControl
from mediatools.gui.shrinkmonitor import ShrinkMonitor
from mediatools.shrink import ShrinkOptions
from mediatools.shrink.slottable import SlotTable


class LateReporter(BarrierEncoder):
    """Reports progress like ffmpeg: after both encodes have started."""

    def encode(self, src: str, dst: str,
               on_progress: Callable[[float], None] | None = None) -> bool:
        """Report a file-specific fraction a little later, then encode.

        Args:
            src: Input.
            dst: Output.
            on_progress: Progress callback.

        Returns:
            True.
        """
        time.sleep(0.2)
        if on_progress is not None:
            on_progress(0.25 if os.path.basename(src).startswith("a") else 0.75)
        time.sleep(0.4)
        return super().encode(src, dst, None)


def test_every_slot_shows_its_own_progress(tmp_path: Path) -> None:
    """Each slot's callback writes into its own cell (no 0 % slot).

    Args:
        tmp_path: Temporary directory.
    """
    for name in ("a", "b"):
        make_file(tmp_path / f"c/{name}.mp4", name.encode() * 100)
    shrinker, _ = build(tmp_path, ShrinkOptions(jobs=2), LateReporter())
    seen: list[list[tuple[str, float]]] = []
    collect = shrinker._collect                                 # pylint: disable=protected-access

    def spy(table: SlotTable, future: Future, busy: int) -> None:
        """Record the cells, then collect as usual.

        Args:
            table: Slot bookkeeping.
            future: Finished encode.
            busy: Running encodes.
        """
        seen.append(sorted((cell[0], cell[1]) for cell in table.cells()))
        collect(table, future, busy)

    # pylint: disable-next=protected-access
    shrinker._collect = spy                                     # type: ignore[method-assign]
    assert shrinker.run() == 0
    assert seen[0] == [("a.mp4", 0.25), ("b.mp4", 0.75)]


def test_file_lines_use_the_speed_of_the_whole_run() -> None:
    """A file at 0 % gets an ETA from all running files, not a huge one."""
    display = ProgressBar(2000, 2, None, stream=io.StringIO())
    display.started -= 600                                      # 10 minutes running
    display.show_many([("a.mp4", 0.0, 1000), ("b.mp4", 0.6, 1000)])
    speed = display.rate(600)
    assert speed is not None
    first = display.file_line("a.mp4", 0.0, 1000, share=2, speed=speed)
    assert "| 33m left |" in first                              # 1000 B at 300 B/600 s, half each


def test_panel_shows_all_file_lines_and_one_space_line(tmp_path: Path) -> None:
    """The status file's space line is dropped; every file line is kept.

    Args:
        tmp_path: Temporary directory.
    """
    control = RunControl("shrink", DataDir(str(tmp_path / "data")))
    os.makedirs(os.path.dirname(control.status_file), exist_ok=True)
    Path(control.status_file).write_text(
        "job   [##..] 10.0 % | x\nfile  1 % | a.mp4\nfile  2 % | b.mp4\nspace  session x\n",
        encoding="utf-8")
    control.active_pid = os.getpid                              # type: ignore[method-assign]
    control.active_log = lambda: None                           # type: ignore[method-assign]
    state = ShrinkMonitor(control=control).read()
    assert state.progress == ["job   [##..] 10.0 % | x", "file  1 % | a.mp4",
                              "file  2 % | b.mp4"]
    assert state.percent == 10.0
