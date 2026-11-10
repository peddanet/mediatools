"""1.2.16: the progress display tells read, written and freed space apart."""
import io
import logging
from pathlib import Path

import pytest

from test_shrink_run import build, videos
from mediatools.core.attachview import AttachView
from mediatools.core.progressbar import ProgressBar
from mediatools.core.runcontrol import RunControl
from mediatools.shrink import ShrinkOptions, ThroughputLog
from mediatools.shrink.spacetally import SpaceTally

GIB = 1024 ** 3


class Tty(io.StringIO):
    """String buffer that claims to be a terminal."""

    def isatty(self) -> bool:
        """Claim a terminal.

        Returns:
            True.
        """
        return True


def test_tally_separates_run_and_overall(tmp_path: Path) -> None:
    """This run starts at zero; overall adds the throughput log totals.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    log = ThroughputLog(str(tmp_path / "speed.json"))
    log.add(100 * GIB, 60.0, 15 * GIB)
    tally = SpaceTally.from_throughput(log)
    assert (tally.run_src, tally.run_out) == (0, 0)
    tally.add(10 * GIB, 2 * GIB)
    assert tally.overall() == (110 * GIB, 17 * GIB)
    line = tally.line()
    assert line.startswith("space  this run: 10.0 GiB read -> 2.0 GiB written "
                           "= 8.0 GiB freed | overall: 110.0 GiB read")
    assert line.endswith("17.0 GiB written = 93.0 GiB freed")
    summary = tally.summary()
    assert summary[0].endswith("8.0 GiB freed (output 20.0 % of the source)")
    assert summary[1].startswith("space overall since 1.2.1: 110.0 GiB read")
    assert not SpaceTally().summary()
    assert "this run: 0.0 GiB read" in SpaceTally(GIB, GIB // 2).summary()[0]


def test_overall_line_says_source() -> None:
    """The GiB of the overall line are marked as source bytes."""
    pbar = ProgressBar(2 * GIB, 2, stream=io.StringIO())
    pbar.file_done(GIB)
    assert "| 1.0/2.0 GiB source | ETA" in pbar.line()


def test_third_line_is_drawn_and_cleared() -> None:
    """With a space line three lines are redrawn in place and cleared."""
    pbar = ProgressBar(1000, 2, stream=Tty())
    pbar.show("a.mp4", 0.5, 100, "space  this run")
    pbar.show("a.mp4", 0.6, 100, "space  this run")
    text = pbar.stream.getvalue()
    assert pbar.drawn == 3 and text.count("\n") == 4
    assert "\r\033[1A\033[1A\033[K" in text           # second draw: back two lines
    pbar.clear()
    assert pbar.drawn == 0
    assert pbar.stream.getvalue().endswith("\r\033[K" + "\033[1A\r\033[K" * 2)


def test_fewer_lines_clear_the_old_ones_first() -> None:
    """Dropping from three lines to two leaves no stale third line."""
    pbar = ProgressBar(1000, 2, stream=Tty())
    pbar.show("a.mp4", 0.5, 100, "space  this run")
    pbar.show("a.mp4", 0.6, 100)
    assert pbar.drawn == 2
    assert "\033[1A\r\033[K" * 2 in pbar.stream.getvalue()


def test_status_file_carries_the_space_line(tmp_path: Path) -> None:
    """``mediatools attach`` gets the third line through the status file.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    pbar = ProgressBar(1000, 2, stream=io.StringIO())
    pbar.status_path = str(tmp_path / "s.status")
    pbar.show("a.mp4", 0.5, 100, "space  this run: x")
    lines = Path(pbar.status_path).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3 and lines[2] == "space  this run: x"


@pytest.mark.usefixtures("datadir")
def test_attach_draws_two_or_three_lines() -> None:
    """The attach view follows the number of lines in the status file."""
    control = RunControl("shrink")
    out = Tty()
    view = AttachView(control, out)
    Path(control.status_file).write_text("total\nfile\nspace\n", encoding="utf-8")
    view._draw()  # pylint: disable=protected-access
    assert view.drawn == 3 and out.getvalue().endswith("\033[Kfile\n\033[Kspace")
    Path(control.status_file).write_text("total\nfile\n", encoding="utf-8")
    view._draw()  # pylint: disable=protected-access
    assert view.drawn == 2 and out.getvalue().endswith("\033[Ktotal\n\033[Kfile")
    view._clear()  # pylint: disable=protected-access
    assert view.drawn == 0


def test_shrink_run_counts_and_logs_freed_space(tmp_path: Path,
                                                caplog: pytest.LogCaptureFixture) -> None:
    """A run counts replaced files only and logs both views at the end.

    Args:
        tmp_path: Temporary directory (fixture).
        caplog: Log capture (fixture).
    """
    videos(tmp_path)
    shrinker, _encoder = build(tmp_path)
    with caplog.at_level(logging.INFO, logger="test"):
        assert shrinker.run() == 0
    space = shrinker.state["space"]
    written = sum(len(b"shrunk:" + name.encode())
                  for name in ("small.mp4", "large.mp4", "medium.mp4"))
    assert (space.run_src, space.run_out) == (1110, written)
    assert space.overall() == (1110, written)
    messages = [record.getMessage() for record in caplog.records]
    assert any(text.startswith("space this run: 0.0 GiB read") for text in messages)
    assert any(text.startswith("space overall since 1.2.1:") for text in messages)


def test_kept_copies_free_no_space(tmp_path: Path) -> None:
    """With ``--keep-copies`` the originals stay, so nothing counts as freed.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    videos(tmp_path)
    shrinker, _encoder = build(tmp_path, ShrinkOptions(keep_copies=True))
    assert shrinker.run() == 0 and shrinker.stats["copies"] == 3
    assert (shrinker.state["space"].run_src, shrinker.state["space"].run_out) == (0, 0)
