"""Unit tests for mediatools.sync."""
import logging
import os
from pathlib import Path

from conftest import make_file, needs_rsync, read
from mediatools.core.prompt import Prompt
from mediatools.flatten import Flattener
from mediatools.sync import FsCheck, RsyncRunner, Syncer, TargetReport

LOG = logging.getLogger("test")


def test_fscheck_hardlink_and_nesting(tmp_path: Path) -> None:
    """The hard-link test works and nested paths are detected.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    ok, why = FsCheck.can_hardlink(str(tmp_path))
    assert ok, why
    assert FsCheck.nested(str(tmp_path), str(tmp_path / "x"))
    assert not FsCheck.nested(str(tmp_path / "a"), str(tmp_path / "b"))
    assert FsCheck().fs_type(str(tmp_path))


def test_rsync_command_never_deletes() -> None:
    """The rsync command preserves hard links and has no --delete."""
    cmd = RsyncRunner("/a", "/b").command(dry_run=False)
    assert "-aHu" in cmd and "--no-inc-recursive" in cmd
    assert not any(arg.startswith("--delete") for arg in cmd)
    assert cmd[-2:] == ["/a/", "/b/"]


def test_rsync_parse_itemize() -> None:
    """Itemize codes are counted per category."""
    out = RsyncRunner.parse(">f+++++++++ a.mp4\n>f.st...... b.mp4\n"
                            "hf+++++++++ _flat/a.mp4 => a.mp4\ncd+++++++++ d/\n")
    assert (out["new"], out["update"], out["hardlink"], out["newdir"]) == (1, 1, 1, 1)


@needs_rsync
def test_sync_preserves_hardlinks_and_never_deletes(tmp_path: Path) -> None:
    """Hard links arrive as hard links; target-only files survive.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    src, dst = tmp_path / "src", tmp_path / "dst"
    make_file(src / "unit/posts/v.mp4", b"video" * 100)
    flattener = Flattener(str(src), LOG)
    flattener.apply(flattener.analyse())
    make_file(dst / "unit/only_on_target.mp4", b"old")
    make_file(dst / "unit/dup_on_target.mp4", b"video" * 100)

    syncer = Syncer(str(src), str(dst), LOG, Prompt(assume_yes=True), report=False)
    assert syncer.run() == 0
    original = os.stat(dst / "unit/posts/v.mp4")
    link = os.stat(dst / "unit/_flat/posts__v.mp4")
    assert original.st_ino == link.st_ino and original.st_nlink == 2
    assert os.path.exists(dst / "unit/only_on_target.mp4")

    result = TargetReport(str(src), str(dst)).analyse(all_duplicates=True)
    status = {rel: state for rel, _size, state, _other in result["target_only"]}
    assert status["unit/only_on_target.mp4"] == "unique"
    assert status["unit/dup_on_target.mp4"] == "duplicate"
    assert any("unit/dup_on_target.mp4" in group for group in result["dup_groups"])


@needs_rsync
def test_sync_update_does_not_overwrite_newer_target(tmp_path: Path) -> None:
    """A newer file on the receiver is kept (rsync -u).

    Args:
        tmp_path: Temporary directory (fixture).
    """
    src, dst = tmp_path / "src", tmp_path / "dst"
    old = make_file(src / "a.mp4", b"original big")
    new = make_file(dst / "a.mp4", b"shrunk")
    os.utime(old, (1_000_000, 1_000_000))
    Syncer(str(src), str(dst), LOG, Prompt(assume_yes=True), report=False).run()
    assert read(new) == b"shrunk"
