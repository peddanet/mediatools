"""Unit tests for mediatools.core."""
import io
import os
from pathlib import Path

from conftest import make_file
from mediatools.core import DataDir, MediaTypes, Prompt, TreeWalker
from mediatools.core.progressbar import ProgressBar


def test_media_types() -> None:
    """Extensions are classified case-insensitively and parsed from CSV."""
    assert MediaTypes.is_media("a.MP4")
    assert MediaTypes.is_video("x.mkv")
    assert not MediaTypes.is_media("shrink.db")
    assert MediaTypes.parse_extensions("mp4, .MOV,") == frozenset({".mp4", ".mov"})


def test_datadir_uses_env(datadir: Path) -> None:
    """The data root follows MEDIATOOLS_HOME.

    Args:
        datadir: Temporary data directory (fixture).
    """
    data = DataDir()
    assert data.root == str(datadir)
    path = data.file("logs", "x", ".log")
    assert os.path.dirname(path) == os.path.join(str(datadir), "logs")


def test_walker_skips_reserved_and_hidden(tmp_path: Path) -> None:
    """_flat, newShrinkedFiles, hidden dirs and symlinks are never yielded.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    for rel in ("a/v.mp4", "a/_flat/v.mp4", "a/newShrinkedFiles/v.mp4", "a/.hidden/v.mp4"):
        make_file(tmp_path / rel)
    os.symlink(tmp_path / "a/v.mp4", tmp_path / "a/link.mp4")
    found = [os.path.relpath(p, tmp_path) for p in TreeWalker().media_files(str(tmp_path))]
    assert found == ["a/v.mp4"]


def test_prompt_answers() -> None:
    """Invalid answers are repeated; empty input returns the default."""
    answers = iter(["maybe", "y"])
    assert Prompt(input_func=lambda _q: next(answers)).yes_no("?") is True
    assert Prompt(input_func=lambda _q: "").yes_no("?", default=False) is False
    assert Prompt(assume_yes=True).yes_no("?") is True


def test_prompt_repeats_invalid_answer_and_handles_eof() -> None:
    """An invalid answer is asked again; end of input gives the default."""
    answers = ["maybe", "j"]
    assert Prompt(input_func=lambda _q: answers.pop(0)).yes_no("Go?")

    def closed(question: str) -> str:
        """Simulate a closed terminal.

        Args:
            question: Ignored.

        Raises:
            EOFError: Always.
        """
        raise EOFError(question)

    assert Prompt(input_func=closed).text("Where?", "/srv") == "/srv"
    assert Prompt(input_func=closed).yes_no("Go?", default=True)


def test_progress_bar_texts() -> None:
    """Remaining time, long names and duration formats of the progress line."""
    class Tty(io.StringIO):
        """Text stream that claims to be a terminal."""

        def isatty(self) -> bool:
            """Claim a terminal.

            Returns:
                True.
            """
            return True

    pbar = ProgressBar(total_bytes=1000, total_files=3, stream=Tty())
    assert pbar.remaining_seconds() is None
    assert "?" in pbar.line("x" * 50)
    pbar.file_done(500)
    pbar.file_done(250)
    assert pbar.remaining_seconds() is not None
    assert "y" not in pbar.line("y" * 50, 0.5, 100)      # file details are on line 2
    line = pbar.file_line("a" * 30 + "b" * 30, 0.5, 1024 ** 3, width=70)
    assert line.startswith("file   50 % of 1.00 GiB (0.50 GiB encoded) | ")
    assert len(line) == 70 and line.endswith("a...bbbbbb")
    assert ProgressBar.duration_text(90061) == "1d01h"
    assert ProgressBar.duration_text(3700) == "1h01m"
    assert ProgressBar.duration_text(120) == "2m"
    pbar.show("z", 0.25, 100)
    pbar.show("z", 0.5, 100)
    assert pbar.stream.getvalue().count("\n") == 2   # second draw reuses both lines
    assert "\033[1A" in pbar.stream.getvalue() and pbar.drawn
    pbar.clear()
    assert not pbar.drawn and pbar.stream.getvalue().endswith("\033[1A\r\033[K")
    pbar.clear()                                       # nothing drawn: no output
    assert pbar.stream.getvalue().endswith("\033[1A\r\033[K")
