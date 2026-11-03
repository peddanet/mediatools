"""Unit tests for mediatools.flatten."""
import logging
import os
from pathlib import Path

from conftest import make_file
from mediatools.flatten import FlatIndex, Flattener, FlatNamer, FlatPlan

LOG = logging.getLogger("test")


# -- FlatNamer -----------------------------------------------------------
def test_namer_default_abbreviations_are_generic() -> None:
    """Without configuration only generic media folder names are shortened."""
    assert FlatNamer.DEFAULT_ABBREV == {"Videos": "V", "Images": "I", "Audios": "U"}


def test_namer_level0() -> None:
    """Short paths keep every component."""
    assert FlatNamer(255).name(["posts", "public", "Videos", "a.mp4"]) == \
        ("posts__public__Videos__a.mp4", 0)


def test_namer_loose_file_keeps_name() -> None:
    """A file directly in the unit keeps its name."""
    assert FlatNamer(143).name(["clip.mp4"]) == ("clip.mp4", 0)


def test_namer_level1_abbreviates() -> None:
    """Level 1 abbreviates folders and compacts id/date folders."""
    parts = ["attachments", "members", "987654321 2024-05-01 10-00-00", "Videos",
             "x" * 70 + "_a1b2c3d4e5_clip.mp4"]
    name, level = FlatNamer(143, {"attachments": "a", "members": "m", "Videos": "V"}).name(parts)
    assert level == 1
    assert name.startswith("a__m__987654321-20240501T100000__V__")


def test_namer_level2_keeps_tail_and_limit() -> None:
    """Level 2 cuts the front of the stem and keeps the media id."""
    parts = ["attachments", "members", "987654321 2024-05-01 10-00-00", "Videos",
             "y" * 150 + "_a1b2c3d4e5_clip.mp4"]
    namer = FlatNamer(143)
    name, level = namer.name(parts)
    assert level == 2
    assert namer.nbytes(name) <= 143
    assert name.endswith("_a1b2c3d4e5_clip.mp4")
    assert FlatNamer.CUT in name


def test_namer_counts_bytes_not_chars() -> None:
    """Multi-byte characters are counted in bytes."""
    namer = FlatNamer(20)
    name, _level = namer.name(["ä" * 30 + ".mp4"])
    assert namer.nbytes(name) <= 20


def test_namer_disambiguate_is_stable() -> None:
    """The collision suffix depends only on the source path."""
    namer = FlatNamer(143)
    first = namer.disambiguate("x.mp4", "A/x.mp4")
    assert first == namer.disambiguate("x.mp4", "A/x.mp4")
    assert first != namer.disambiguate("x.mp4", "B/x.mp4")


# -- Flattener -----------------------------------------------------------
def build_tree(root: Path) -> None:
    """Create a small collection with two units.

    Args:
        root: Master directory.
    """
    make_file(root / "projectA/posts/public/Videos/v1.mp4", b"1")
    make_file(root / "projectA/attachments/public/123456 2024-05-01 10-00-00/Images/i1.jpg", b"2")
    make_file(root / "projectA/loose.mp4", b"3")
    make_file(root / "projectA/posts/public/Videos/shrink.db", b"db")
    make_file(root / "projectA/posts/public/Videos/newShrinkedFiles/v1.mp4", b"tmp")
    make_file(root / "projectB/photos/p.png", b"4")
    os.symlink(root / "projectA/loose.mp4", root / "projectA/link.mp4")


def test_flatten_creates_hardlinks(tmp_path: Path) -> None:
    """Media files get hard links; db, temp files and symlinks do not.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    build_tree(tmp_path)
    flattener = Flattener(str(tmp_path), LOG)
    plan = flattener.analyse()
    assert plan.counts() == {"link": 4}
    assert flattener.apply(plan)["link"] == 4
    flat = tmp_path / "projectA/_flat"
    names = sorted(n for n in os.listdir(flat) if n != FlatIndex.NAME)
    assert names == ["attachments__public__123456 2024-05-01 10-00-00__Images__i1.jpg",
                     "loose.mp4", "posts__public__Videos__v1.mp4"]
    assert os.path.samefile(flat / "posts__public__Videos__v1.mp4",
                            tmp_path / "projectA/posts/public/Videos/v1.mp4")
    assert FlatIndex(str(flat)).load().entries["loose.mp4"] == "loose.mp4"


def test_flatten_is_idempotent(tmp_path: Path) -> None:
    """A second run finds everything already linked.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    build_tree(tmp_path)
    flattener = Flattener(str(tmp_path), LOG)
    flattener.apply(flattener.analyse())
    assert flattener.analyse().counts() == {"ok": 4}


def test_flatten_relink_after_replacement(tmp_path: Path) -> None:
    """A replaced source (new inode, same path) is re-linked.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    build_tree(tmp_path)
    flattener = Flattener(str(tmp_path), LOG)
    flattener.apply(flattener.analyse())
    src = tmp_path / "projectA/posts/public/Videos/v1.mp4"
    os.remove(src)
    make_file(src, b"new content")
    plan = flattener.analyse()
    assert len(plan.by_action("relink")) == 1
    flattener.apply(plan)
    assert os.path.samefile(tmp_path / "projectA/_flat/posts__public__Videos__v1.mp4", src)


def test_flatten_prune_and_undo(tmp_path: Path) -> None:
    """Orphans are pruned only on request; undo removes the plan's links.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    build_tree(tmp_path)
    flattener = Flattener(str(tmp_path), LOG)
    plan = flattener.analyse()
    flattener.apply(plan)
    os.remove(tmp_path / "projectB/photos/p.png")
    assert not Flattener(str(tmp_path), LOG).analyse().by_action("prune")
    pruner = Flattener(str(tmp_path), LOG, prune=True)
    assert pruner.apply(pruner.analyse())["prune"] == 1
    assert not os.path.exists(tmp_path / "projectB/_flat/photos__p.png")
    path = tmp_path / "plan.tsv"
    plan.save(str(path))
    assert flattener.undo(FlatPlan.load(str(path))) == 3
    assert os.path.exists(tmp_path / "projectA/posts/public/Videos/v1.mp4")


def test_flatten_never_touches_originals(tmp_path: Path) -> None:
    """Content and mtime of all originals stay unchanged.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    build_tree(tmp_path)
    before = {str(p): os.stat(p).st_mtime_ns for p in tmp_path.rglob("*")
              if p.is_file() and not p.is_symlink()}
    flattener = Flattener(str(tmp_path), LOG)
    flattener.apply(flattener.analyse())
    for path, mtime in before.items():
        assert os.stat(path).st_mtime_ns == mtime
