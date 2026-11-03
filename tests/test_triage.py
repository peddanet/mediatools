"""Unit tests for mediatools.triage."""
import logging
import os
import signal
import time
from pathlib import Path

from conftest import make_file
from mediatools.core import Prompt
from mediatools.flatten import Flattener
from mediatools.triage import (CandidateFinder, DurationCache, FileRemover, FlatLinkResolver,
                               KeptList, PlayerSession, Triage)

LOG = logging.getLogger("test")


def test_kept_list_roundtrip(tmp_path: Path) -> None:
    """Kept files are remembered until they change.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    path = make_file(tmp_path / "a.mp4", b"1")
    kept = KeptList(str(tmp_path / "state/kept.tsv"))
    kept.add([path])
    assert KeptList(kept.path).contains(path)
    make_file(tmp_path / "a.mp4", b"22")
    assert not KeptList(kept.path).contains(path)


def test_player_argv_defaults() -> None:
    """VLC (also as Flatpak) and mpv get safe defaults."""
    vlc = PlayerSession.build_argv("vlc", audio=False)
    assert "--no-one-instance" in vlc and "--no-audio" in vlc
    flatpak = PlayerSession.build_argv("flatpak run org.videolan.VLC", audio=True)
    assert "--no-one-instance" in flatpak and "--no-audio" not in flatpak
    assert "--mute=yes" in PlayerSession.build_argv("mpv", audio=False)


def test_player_classification(tmp_path: Path, fake_player: str) -> None:
    """A closed window is 'closed', running ones are 'open'.

    Args:
        tmp_path: Temporary directory (fixture).
        fake_player: Stand-in player script (fixture).
    """
    files = [make_file(tmp_path / f"{i}.mp4") for i in range(3)]
    session = PlayerSession(fake_player, start_window=0.5, stagger=0.05)
    session.argv = [fake_player]
    session.open(files)
    os.kill(session.items[0]["proc"].pid, signal.SIGUSR1)     # user closes window 0
    time.sleep(0.3)
    closed, still_open, errors = session.classify()
    session.close_all()
    assert closed == [files[0]] and still_open == files[1:] and not errors


def test_player_early_exit_is_error(tmp_path: Path) -> None:
    """A player that exits at once is classified as error, never as closed.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    path = make_file(tmp_path / "a.mp4")
    session = PlayerSession("true", start_window=0.3, stagger=0)
    session.argv = ["true"]
    session.open([path])
    closed, _still_open, errors = session.classify()
    assert not closed and errors[0][0] == path


def test_flat_link_resolver(tmp_path: Path) -> None:
    """The _flat link of a file is found by inode.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    src = make_file(tmp_path / "unit/posts/v.mp4")
    flattener = Flattener(str(tmp_path), LOG)
    flattener.apply(flattener.analyse())
    names = FlatLinkResolver(str(tmp_path)).flat_names(src)
    assert names == [str(tmp_path / "unit/_flat/posts__v.mp4")]


class FakePrompt:
    """Prompt double with fixed answers."""

    def __init__(self, text_answer: str = "", yes: bool = True) -> None:
        """Store the answers.

        Args:
            text_answer: Answer to every text question.
            yes: Answer to every yes/no question.
        """
        self.text_answer = text_answer
        self.yes = yes

    def text(self, *_args, **_kwargs) -> str:
        """Answer a text question.

        Args:
            *_args: Ignored.
            **_kwargs: Ignored.

        Returns:
            The fixed text answer.
        """
        return self.text_answer

    def yes_no(self, *_args, **_kwargs) -> bool:
        """Answer a yes/no question.

        Args:
            *_args: Ignored.
            **_kwargs: Ignored.

        Returns:
            The fixed yes/no answer.
        """
        return self.yes


class FakePlayer:
    """Player double: pretends the user closed the first window."""

    def __init__(self) -> None:
        """Start with an empty batch."""
        self.paths = []

    def open(self, paths: list[str]) -> None:
        """Remember the batch.

        Args:
            paths: Files of the batch.
        """
        self.paths = paths

    def classify(self) -> tuple[list[str], list[str], list]:
        """Report the first file as closed, the others as open.

        Returns:
            ``(closed, still_open, errors)``.
        """
        return self.paths[:1], self.paths[1:], []

    def close_all(self) -> None:
        """Nothing to close."""


def test_triage_removes_original_and_flat_link(tmp_path: Path) -> None:
    """Closing a window removes the video and its _flat link; open ones stay.

    A 0-byte placeholder keeps the original name and the removal is logged.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    first = make_file(tmp_path / "unit/posts/a.mp4", b"a")
    second = make_file(tmp_path / "unit/posts/b.mp4", b"b")
    flattener = Flattener(str(tmp_path), LOG)
    flattener.apply(flattener.analyse())
    kept = KeptList(str(tmp_path / "kept.tsv"))
    triage = Triage(str(tmp_path), LOG, finder=[(first, 1.0), (second, 1.0)],
                    player=FakePlayer(), remover=FileRemover(permanent=True, logger=LOG),
                    kept=kept, batch=10, prompt=FakePrompt(),
                    discarded_log=str(tmp_path / "discarded.tsv"))
    assert triage.run() == 0
    assert os.path.getsize(first) == 0                                   # placeholder
    assert not os.path.exists(tmp_path / "unit/_flat/posts__a.mp4")
    assert first in (tmp_path / "discarded.tsv").read_text(encoding="utf-8")
    assert os.path.exists(second) and os.path.exists(tmp_path / "unit/_flat/posts__b.mp4")
    assert kept.contains(second)


def test_triage_not_confirmed_removes_nothing(tmp_path: Path) -> None:
    """Without confirmation nothing is removed.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    path = make_file(tmp_path / "unit/a.mp4")
    triage = Triage(str(tmp_path), LOG, finder=[(path, 1.0)], player=FakePlayer(),
                    remover=FileRemover(permanent=True),
                    kept=KeptList(str(tmp_path / "k.tsv")), prompt=FakePrompt(yes=False))
    triage.run()
    assert os.path.exists(path)


def test_triage_without_placeholder_deletes(tmp_path: Path) -> None:
    """With placeholder=False the name disappears completely.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    first = make_file(tmp_path / "unit/a.mp4", b"a")
    second = make_file(tmp_path / "unit/b.mp4", b"b")
    triage = Triage(str(tmp_path), LOG, finder=[(first, 1.0), (second, 1.0)],
                    player=FakePlayer(), remover=FileRemover(permanent=True, logger=LOG),
                    kept=KeptList(str(tmp_path / "kept.tsv")), prompt=FakePrompt(),
                    placeholder=False, discarded_log=str(tmp_path / "d.tsv"))
    assert triage.run() == 0
    assert not os.path.exists(first) and os.path.exists(second)


def test_placeholder_never_overwrites(tmp_path: Path) -> None:
    """If the name is taken again before the placeholder is made, it is kept.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    path = make_file(tmp_path / "a.mp4", b"a")

    class Recreating(FileRemover):
        """Remover whose removal is immediately undone by someone else."""

        def remove(self, path: str) -> bool:
            """Pretend to remove, but leave a new file behind.

            Args:
                path: File to remove.

            Returns:
                Always True.
            """
            Path(path).write_bytes(b"new")
            return True

    assert not Recreating(permanent=True, logger=LOG).discard(path)
    assert Path(path).read_bytes() == b"new"


def test_announce_counts_and_estimates(tmp_path: Path) -> None:
    """The start message reports count, batches and asks only if wanted.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    candidates = [(str(tmp_path / f"{n}.mp4"), 30.0) for n in range(25)]
    triage = Triage(str(tmp_path), LOG, finder=candidates, player=FakePlayer(),
                    remover=FileRemover(permanent=True, logger=LOG),
                    kept=KeptList(str(tmp_path / "kept.tsv")), batch=10,
                    prompt=Prompt(False, lambda _q: "n"), discarded_log=str(tmp_path / "d"))
    assert not triage.announce(candidates, 100)                  # user answers no
    assert triage.announce(candidates, 100, ask=False)            # -y / dry run
    assert not triage.announce([], 100, ask=False)                # nothing to do


def test_duration_cache_and_empty_files(tmp_path: Path) -> None:
    """Cached durations are reused while size and mtime are unchanged.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    video = make_file(tmp_path / "v.mp4", b"video")
    make_file(tmp_path / "placeholder.mp4", b"")

    class CountingProbe:
        """Probe that counts its calls."""

        calls = 0

        def duration(self, _path: str) -> float:
            """Return a fixed duration (the path is ignored).

            Returns:
                Always 5.0.
            """
            CountingProbe.calls += 1
            return 5.0

    cache_file = str(tmp_path / "cache.tsv")
    for _run in range(2):
        finder = CandidateFinder(str(tmp_path), frozenset({".mp4"}), 0, 60,
                                 probe=CountingProbe(),  # type: ignore[arg-type]
                                 cache=DurationCache(cache_file))
        assert finder.collect() == [(video, 5.0)]               # placeholder skipped
    assert CountingProbe.calls == 1
    Path(video).write_bytes(b"changed video")
    assert DurationCache(cache_file).get(video, os.stat(video)) is None
