"""Unit tests for the configuration file and database renaming."""
import logging
import os
from pathlib import Path

import pytest

from conftest import make_file
from mediatools.cli import main
from mediatools.core import Config
from mediatools.shrink import DatabaseRenamer, ShrinkDatabase

LOG = logging.getLogger("test")


def write_ini(datadir: Path, text: str) -> Config:
    """Write an INI file into the test data directory and load it.

    Args:
        datadir: Test data directory (``MEDIATOOLS_HOME``).
        text: INI content.

    Returns:
        The loaded configuration.
    """
    os.makedirs(datadir, exist_ok=True)
    (datadir / Config.FILE).write_text(text, encoding="utf-8")
    return Config()


def test_defaults_without_file(datadir: Path) -> None:
    """Without a file the neutral defaults apply.

    Args:
        datadir: Test data directory (fixture).
    """
    config = Config()
    assert not config.exists()
    assert config.path == str(datadir / Config.FILE)
    assert config.db_name() == "shrink.db"
    assert config.abbreviations() == {"Videos": "V", "Images": "I", "Audios": "U"}
    assert ShrinkDatabase.NAME == "shrink.db"


def test_values_from_file(datadir: Path, tmp_path: Path) -> None:
    """Values from the file override the defaults; ~ is expanded.

    Args:
        datadir: Test data directory (fixture).
        tmp_path: Temporary directory (fixture).
    """
    config = write_ini(datadir, f"[collection]\nroot = {tmp_path}\n[shrink]\ndb_name = x.db\n"
                                "[flatten]\nabbreviations = Long Name=L, Other=O\n")
    assert config.path_value("collection", "root") == str(tmp_path)
    assert config.db_name() == "x.db"
    assert config.abbreviations() == {"Long Name": "L", "Other": "O"}


def test_bad_values_are_explained(datadir: Path) -> None:
    """Invalid entries stop with a message naming the key.

    Args:
        datadir: Test data directory (fixture).
    """
    config = write_ini(datadir, "[flatten]\nabbreviations = NoEquals\n[shrink]\n"
                                "db_name = a/b.db\n[snapshot]\nmax_deletions = many\n")
    with pytest.raises(SystemExit, match="Name=Abbreviation"):
        config.abbreviations()
    with pytest.raises(SystemExit, match="file name"):
        config.db_name()
    with pytest.raises(SystemExit, match="max_deletions"):
        config.integer("snapshot", "max_deletions")


def test_require_path_explains_missing_root() -> None:
    """Without path and configuration the user is told what to set."""
    with pytest.raises(SystemExit, match=r"\[collection\] root is not set"):
        Config().require_path("collection", "root", None)
    assert Config().require_path("collection", "root", "/given") == "/given"


def test_cli_config_init_and_default_path(datadir: Path, tmp_path: Path,
                                          capsys: pytest.CaptureFixture) -> None:
    """``config --init`` writes the template; commands use [collection] root.

    Args:
        datadir: Test data directory (fixture).
        tmp_path: Temporary directory (fixture).
        capsys: Output capture (fixture).
    """
    assert main(["config", "--init"]) == 0
    assert "created" in capsys.readouterr().out
    assert main(["config", "--init"]) == 0
    assert "not changed" in capsys.readouterr().out
    collection = tmp_path / "coll"
    make_file(collection / "unit/a.jpg")
    write_ini(datadir, f"[collection]\nroot = {collection}\n")
    assert main(["flatten", "-y"]) == 0
    assert os.path.exists(collection / "unit/_flat/a.jpg")


def test_renamer_renames_and_respects_conflicts(tmp_path: Path) -> None:
    """Databases and their backups are renamed; existing targets are kept.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    make_file(tmp_path / "a/old.db", b"A")
    make_file(tmp_path / "a/old.db.bak_1", b"B")
    make_file(tmp_path / "b/old.db", b"C")
    make_file(tmp_path / "b/new.db", b"D")
    renamer = DatabaseRenamer(str(tmp_path), "old.db", "new.db", LOG)
    plan = renamer.plan()
    assert [status for _src, _dst, status in plan] == ["rename", "rename", "conflict"]
    assert renamer.apply(plan) == {"renamed": 2, "conflict": 1, "error": 0}
    assert (tmp_path / "a/new.db").read_bytes() == b"A"
    assert (tmp_path / "a/new.db.bak_1").read_bytes() == b"B"
    assert (tmp_path / "b/new.db").read_bytes() == b"D"
    assert (tmp_path / "b/old.db").exists()
    with pytest.raises(ValueError):
        DatabaseRenamer(str(tmp_path), "same.db", "same.db", LOG)


def test_cli_rename_db(datadir: Path, tmp_path: Path) -> None:
    """``rename-db`` uses [shrink] db_name as the new name.

    Args:
        datadir: Test data directory (fixture).
        tmp_path: Temporary directory (fixture).
    """
    make_file(tmp_path / "c/d/legacy.db")
    write_ini(datadir, "[shrink]\ndb_name = fresh.db\n")
    assert main(["rename-db", "legacy.db", str(tmp_path / "c"), "--dry-run"]) == 0
    assert (tmp_path / "c/d/legacy.db").exists()
    assert main(["rename-db", "legacy.db", str(tmp_path / "c"), "-y"]) == 0
    assert (tmp_path / "c/d/fresh.db").exists()
