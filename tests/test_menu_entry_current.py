"""The menu entry never points to a program that is gone (dev-conventions, Appendix C item 10)."""
import os
from pathlib import Path

import pytest

from test_release_1140 import fixture_world, install   # noqa: F401  pylint: disable=unused-import
from mediatools.gui.desktopentry import DesktopEntry


def test_install_rewrites_the_menu_entry_and_keeps_the_link_until_then(
        world: dict[str, Path]) -> None:
    """``install.sh`` points the entry to the new link; the old link stays while named.

    Args:
        world: Paths from the fixture.
    """
    entry = world["home"] / ".local" / "share" / "applications" / "mediatools.desktop"
    entry.parent.mkdir(parents=True)
    entry.write_text(f'[Desktop Entry]\nName=mediatools\nExec="{world["old_link"]}" gui\n')
    unit = world["home"] / ".config" / "systemd" / "user" / "mediatools-snapshot.service"
    unit.parent.mkdir(parents=True)
    unit.write_text("[Service]\nExecStart=/opt/other snapshot run -y\n")
    res = install(world, local_bin_in_path=False)
    assert res.returncode == 0, res.stderr
    new_link = world["home"] / ".local" / "bin" / "mediatools"
    assert f'Exec="{new_link}" gui' in entry.read_text()
    assert "menu entry now starts" in res.stdout
    # the unit names the old link: this run keeps the link and rewrites the unit,
    # the next run removes the link
    unit.write_text(f"[Service]\nExecStart={world['old_link']} snapshot run -y\n")
    res = install(world, local_bin_in_path=True)
    assert os.path.lexists(world["old_link"]) and "removed the old link" not in res.stdout
    assert f"ExecStart={new_link} snapshot run -y" in unit.read_text()
    res = install(world, local_bin_in_path=True)
    assert res.returncode == 0 and "removed the old link" in res.stdout


def test_existing_entry_is_kept_current_without_the_setting(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An existing outdated entry is repaired; none is created unless wanted.

    Args:
        tmp_path: Temporary directory (fixture).
        monkeypatch: Patcher (fixture).
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    program = tmp_path / "mediatools"
    program.write_text("#!/bin/sh\n", encoding="utf-8")
    entry = DesktopEntry(str(program))
    assert entry.repair(False) == [] and not os.path.exists(entry.path)
    os.makedirs(os.path.dirname(entry.path))
    Path(entry.path).write_text('[Desktop Entry]\nExec="/gone/mediatools" gui\n',
                                encoding="utf-8")
    repaired = entry.repair(False)
    assert "entry outdated: " + entry.path in repaired and not entry.problems()
    assert f'Exec="{program}" gui' in Path(entry.path).read_text(encoding="utf-8")
