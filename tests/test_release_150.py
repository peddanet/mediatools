"""Release 1.5.0: configuration, state and data in the XDG folders."""
import stat
from pathlib import Path

import pytest
from test_privacy_scan import privacy_scan

from mediatools.cli import Cli
from mediatools.core.config import Config
from mediatools.core.datadir import DataDir
from mediatools.core.datamigration import DataMigration
from mediatools.core.placesreport import PlacesReport
from mediatools.core.privacylists import PrivacyLists
from mediatools.gui.catalog import CommandCatalog
from mediatools.gui.commandspec import CommandSpec
from mediatools.gui.optionspec import OptionSpec


@pytest.fixture(name="home")
def fixture_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A home directory without MEDIATOOLS_HOME and XDG variables.

    Args:
        tmp_path: Temporary directory (fixture).
        monkeypatch: Environment patcher (fixture).

    Returns:
        The home directory.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in ("MEDIATOOLS_HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        monkeypatch.delenv(name, raising=False)
    return home


def legacy(home: Path) -> Path:
    """Create an old data folder with typical content.

    Args:
        home: Home directory.

    Returns:
        The old folder.
    """
    old = home / "scripts" / "mediatools.d"
    for sub in ("logs", "state", "reports", "plans", "releases", "venv/bin"):
        (old / sub).mkdir(parents=True)
    (old / "logs" / "shrink_x.log").write_text("log", encoding="utf-8")
    (old / "plans" / "flatten_x.tsv").write_text("plan", encoding="utf-8")
    (old / "mediatools.ini").write_text("[collection]\n", encoding="utf-8")
    (old / "privacy_denylist.txt").write_text("secret\n", encoding="utf-8")
    (old / "privacy_allowlist.txt").write_text("sea level\n", encoding="utf-8")
    (old / "notes.txt").write_text("unknown", encoding="utf-8")
    return old


def test_xdg_layout_and_routing(home: Path) -> None:
    """Without old data the XDG defaults are used; plans go to the data folder.

    Args:
        home: Home directory (fixture).
    """
    data = DataDir()
    assert data.mode == "xdg"
    assert data.config_dir == str(home / ".config" / "mediatools")
    assert data.root == data.state_dir == str(home / ".local" / "state" / "mediatools")
    assert data.sub("logs") == str(home / ".local" / "state" / "mediatools" / "logs")
    assert data.sub("plans") == str(home / ".local" / "share" / "mediatools" / "plans")
    assert data.privacy_file("deny") == str(home / ".config/mediatools/privacy/denylist.txt")
    assert Config().path == str(home / ".config" / "mediatools" / "mediatools.ini")


def test_xdg_variables_count_only_when_absolute(home: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    """An absolute XDG variable replaces the default, a relative one is ignored.

    Args:
        home: Home directory (fixture).
        monkeypatch: Environment patcher (fixture).
    """
    monkeypatch.setenv("XDG_STATE_HOME", str(home / "st"))
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative/cfg")
    data = DataDir()
    assert data.state_dir == str(home / "st" / "mediatools")
    assert data.config_dir == str(home / ".config" / "mediatools")


def test_old_folder_stays_in_use_until_moved(home: Path) -> None:
    """While ~/scripts/mediatools.d holds data it is used with the old names.

    Args:
        home: Home directory (fixture).
    """
    old = legacy(home)
    data = DataDir()
    assert data.mode == "legacy" and data.root == str(old)
    assert data.privacy_file("allow") == str(old / "privacy_allowlist.txt")
    assert "move it with: mediatools where --migrate" in PlacesReport(data).lines()[0]


def test_migration_moves_data_and_leaves_the_release(home: Path, tmp_path: Path) -> None:
    """All data moves, venv and unknown files stay, a note is left behind.

    Args:
        home: Home directory (fixture).
        tmp_path: Temporary directory (fixture).
    """
    old = legacy(home)
    (tmp_path / "proc").mkdir()
    moves = DataMigration(proc=str(tmp_path / "proc")).run()
    assert len(moves) == 8
    data = DataDir()
    assert data.mode == "xdg"
    assert (Path(data.state_dir) / "logs" / "shrink_x.log").read_text(encoding="utf-8") == "log"
    assert (Path(data.data_dir) / "plans" / "flatten_x.tsv").exists()
    deny = Path(data.privacy_file("deny"))
    assert deny.read_text(encoding="utf-8") == "secret\n"
    assert stat.S_IMODE(deny.stat().st_mode) == 0o600
    assert stat.S_IMODE(Path(data.config_file).stat().st_mode) == 0o600
    assert sorted(p.name for p in old.iterdir()) == [DataMigration.NOTE, "notes.txt", "venv"]
    assert DataMigration(proc=str(tmp_path / "proc")).moves() == []
    (old / "reports").mkdir()                       # recreated later by an older tool
    assert DataDir().mode == "xdg"


def test_migration_refuses_with_running_process_or_existing_target(home: Path,
                                                                   tmp_path: Path) -> None:
    """Another mediatools process or an existing target stops the move.

    Args:
        home: Home directory (fixture).
        tmp_path: Temporary directory (fixture).
    """
    legacy(home)
    proc = tmp_path / "proc"
    (proc / "4242").mkdir(parents=True)
    (proc / "4242" / "cmdline").write_bytes(b"python3\0/x/venv/bin/mediatools\0gui\0")
    (proc / "77").mkdir()
    (proc / "77" / "cmdline").write_bytes(b"bash\0-c\0echo mediatools later\0")
    (proc / "self").mkdir()
    (proc / "99").mkdir()                                   # no cmdline: ignored
    migration = DataMigration(proc=str(proc))
    assert migration.others() == [(4242, "python3 /x/venv/bin/mediatools gui")]
    (home / ".config" / "mediatools").mkdir(parents=True)
    (home / ".config" / "mediatools" / "mediatools.ini").write_text("", encoding="utf-8")
    problems = migration.problems()
    assert len(problems) == 2 and "pid 4242" in problems[0] and "target exists" in problems[1]
    with pytest.raises(RuntimeError):
        migration.run()
    assert DataMigration(proc=str(tmp_path / "missing")).others() == []


def test_cli_where_and_migrate(home: Path, capsys: pytest.CaptureFixture,
                               monkeypatch: pytest.MonkeyPatch) -> None:
    """``where`` lists the places; ``--migrate`` moves, then has nothing left.

    Args:
        home: Home directory (fixture).
        capsys: Output capture (fixture).
        monkeypatch: Environment patcher (fixture).
    """
    legacy(home)
    monkeypatch.setattr(DataMigration, "others", lambda self: [])
    assert Cli().main(["where", "--migrate"]) == 0
    out = capsys.readouterr().out
    assert "moved" in out and "layout: XDG layout" in out and "denylist" in out
    assert Cli().main(["where", "--migrate"]) == 0
    assert "nothing to move" in capsys.readouterr().out
    monkeypatch.setenv("MEDIATOOLS_HOME", str(home / "x"))
    assert Cli().main(["where", "--migrate"]) == 1


def test_cli_where_reports_refusal(home: Path, capsys: pytest.CaptureFixture,
                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """A refused move is reported with exit code 1.

    Args:
        home: Home directory (fixture).
        capsys: Output capture (fixture).
        monkeypatch: Environment patcher (fixture).
    """
    legacy(home)
    monkeypatch.setattr(DataMigration, "others", lambda self: [(5, "mediatools shrink")])
    assert Cli().main(["where", "--migrate"]) == 1
    assert "not moved: another mediatools process runs" in capsys.readouterr().out


def test_privacy_lists_show_and_edit(home: Path, capsys: pytest.CaptureFixture,
                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """The lists are shown with their size; --edit creates a private file.

    Args:
        home: Home directory (fixture).
        capsys: Output capture (fixture).
        monkeypatch: Environment patcher (fixture).
    """
    assert Cli().main(["privacy"]) == 0
    assert capsys.readouterr().out.count("(missing)") == 2
    monkeypatch.setenv("EDITOR", "true")
    assert Cli().main(["privacy", "--edit", "deny"]) == 0
    path = Path(PrivacyLists().path("deny"))
    assert path == home / ".config" / "mediatools" / "privacy" / "denylist.txt"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    path.write_text(path.read_text(encoding="utf-8") + "term\n", encoding="utf-8")
    assert "(1 entries)" in PrivacyLists().lines()[0]
    assert PrivacyLists().ensure("deny") == str(path)           # existing file unchanged
    assert privacy_scan.PrivacyScan.read_list("privacy_denylist.txt") == ["term"]
    assert privacy_scan.PrivacyScan.read_list("other.txt") == []


def test_gui_where_needs_confirmation_only_for_migrate() -> None:
    """``where`` is read-only unless --migrate is ticked; ``privacy`` has no form."""
    spec = CommandSpec("where", options=[OptionSpec(flags=["--migrate"], dest="migrate",
                                                    kind="flag")])
    assert not spec.changes_data({"migrate": False})
    assert spec.changes_data({"migrate": True})
    assert not CommandSpec("where").changes_data({})
    names = [item.name for item in CommandCatalog(Cli().parser).commands()]
    assert "where" in names and "privacy" not in names
