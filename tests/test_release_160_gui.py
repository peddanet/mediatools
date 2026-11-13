"""Release 1.6.0: job list, appearance, update bar, menu entry repair."""
import io
import os
import tkinter as tk
from pathlib import Path

import pytest

from test_gui_tk import fixture_root  # pylint: disable=unused-import
from mediatools.cli import Cli
from mediatools.core.config import Config
from mediatools.gui.app import MediaToolsApp
from mediatools.gui.appearance import Appearance
from mediatools.gui.catalog import CommandCatalog
from mediatools.gui.desktopentry import DesktopEntry
from mediatools.gui.formstore import FormStore
from mediatools.gui.joblist import JobList
from mediatools.gui.shrinkmonitor import ShrinkMonitor
from mediatools.gui.updatebar import UpdateBar
from mediatools.shrink.jobregistry import JobEntry
from mediatools.shrink.jobspec import JobSpec
from mediatools.shrink.ledgerrow import LedgerRow


class FakeLook:
    """Desktop settings double.

    Attributes:
        values: Scheme and accent colour returned.
    """

    def __init__(self, scheme: str | None = None, accent: str | None = None) -> None:
        """Store the answers.

        Args:
            scheme: Scheme returned by :meth:`scheme`.
            accent: Colour returned by :meth:`accent`.
        """
        self.values = [scheme, accent]

    def scheme(self) -> str | None:
        """Return the stored scheme.

        Returns:
            ``dark``, ``light`` or None.
        """
        return self.values[0]

    def accent(self) -> str | None:
        """Return the stored accent colour.

        Returns:
            ``#rrggbb`` or None.
        """
        return self.values[1]


def test_appearance_zoom_theme_and_saving(root: tk.Tk) -> None:
    """Zoom is clamped and saved; the dark scheme colours classic widgets.

    Args:
        root: Main window (fixture).
    """
    text = tk.Text(root)
    config = Config()
    look = Appearance(root, config, FakeLook())
    look.apply()
    assert look.points() == 10 and look.style.theme_use() == "clam"
    for _step in range(30):
        look.zoom(1)
    assert look.scale == Appearance.MAX_SCALE and Config().get("gui", "scale") == "2.5"
    assert look.zoom(0) == 1.0
    assert look.set_theme("dark") == "dark" and Config().get("gui", "theme") == "dark"
    assert text.cget("background") == Appearance.PALETTES["dark"]["surface"]
    again = Appearance(root, Config(), FakeLook())
    assert again.theme == "dark" and again.scale == 1.0
    config.set_value("gui", "scale", "huge")
    assert Appearance(root, Config(), FakeLook()).scale == 1.0 and Appearance.clamp(0.1) == 0.8


def test_job_list_fills_and_picks(root: tk.Tk, datadir: Path) -> None:
    """The list shows the jobs with progress; selecting hands the job over.

    Args:
        root: Main window (fixture).
        datadir: Data directory (fixture).
    """
    del datadir
    monitor = ShrinkMonitor()
    ledger_path = monitor.job_files()[1]
    registry = monitor.registry()
    assert registry.ledger is not None
    registry.ledger.add(LedgerRow("2026-11-13 10:00:00", "s", "shrunk", 3 * 1024 ** 3,
                                  1024 ** 3, 60.0, None, None, "/m/c/a.mp4"))
    registry.planned(JobSpec("/m/c"), {"crf": 26, "jobs": 2}, 1024 ** 3, 1)
    picked: list[tuple[str, bool]] = []
    jobs = JobList(root, monitor, lambda entry, start: picked.append((entry.spec.root, start)))
    jobs.refresh()
    rows = jobs.jobs.get_children()
    assert len(rows) == 1 and os.path.exists(ledger_path)
    values = jobs.jobs.item(rows[0], "values")
    assert "75.0 %" in values[1] and values[3] == "crf 26, 2 jobs" and values[4] == "/m/c"
    assert jobs.pick(False) is None                              # nothing selected yet
    jobs.jobs.selection_set(rows[0])
    root.update()
    jobs.pick(True)
    assert ("/m/c", False) in picked and picked[-1] == ("/m/c", True)
    jobs.refresh()                                               # unchanged: kept as is
    assert jobs.jobs.selection() == (rows[0],)


def test_app_fills_the_shrink_form_and_offers_a_restart(root: tk.Tk, tmp_path: Path) -> None:
    """A picked job fills the form; a newer installed release shows the bar.

    Args:
        root: Main window (fixture).
        tmp_path: Temporary directory (fixture).
    """
    asked: list[str] = []
    app = MediaToolsApp(root, CommandCatalog(Cli().parser), FormStore(str(tmp_path / "g.json")),
                        ask=lambda title, _x: not asked.append(title), refresh_ms=0)
    entry = JobEntry(JobSpec("/m/c", frozenset({".mp4", ".mkv"}), 60.0, 300_000_000,
                             JobSpec.parse_date("2026-11-01")), {"crf": 24, "jobs": 2})
    app.forms["shrink"].console.run = lambda args, shown: asked.append(shown) or True
    assert app.pick_job(entry) and app.pick_job(entry, start=True)
    line = app.forms["shrink"].line.get()
    assert "/m/c" in line and "--crf 24" in line and "--jobs 2" in line
    assert "--min-size 300M" in line and "--newer-than 2026-11-01" in line
    assert "--ext mkv,mp4" in line and asked[0] == "Run mediatools shrink"
    restarted: list[bool] = []
    app.restarter = lambda: restarted.append(True)
    app.updates.version_of = lambda: "9.9.9"
    app.updates.check(background=False)
    root.deiconify()
    assert app.updates.show(app.notebook)
    root.update()
    assert "9.9.9 is installed" in app.updates.text.get()
    app.zoom(1)
    assert app.appearance.scale == 1.1
    assert app.restart() and restarted == [True]


def test_update_bar_stays_hidden_for_the_same_release(root: tk.Tk) -> None:
    """No bar while the installed release is the running one.

    Args:
        root: Main window (fixture).
    """
    updates = UpdateBar(root, lambda: None, version_of=lambda: None)
    updates.check(background=False)
    assert not updates.show(root)


def test_menu_entry_is_repaired_when_wanted(tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch,
                                            capsys: pytest.CaptureFixture[str]) -> None:
    """A wanted entry comes back on update; ``where`` reports its state.

    Args:
        tmp_path: Temporary directory (fixture).
        monkeypatch: Patcher (fixture).
        capsys: Output capture (fixture).
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    program = tmp_path / "mediatools"
    program.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(DesktopEntry, "default_program", staticmethod(lambda: str(program)))
    entry = DesktopEntry(str(program))
    assert entry.repair(False) == [] and not os.path.exists(entry.path)
    assert len(entry.repair(True)) == 3 and not entry.problems()
    Path(entry.path).write_text("[Desktop Entry]\n", encoding="utf-8")
    assert entry.problems() == ["entry outdated: " + entry.path]
    os.remove(entry.path)
    Config().set_value("gui", "desktop_entry", "yes")
    monkeypatch.setattr("mediatools.cli.ReleaseUpdater.run", lambda _self: 0)
    assert Cli().main(["update", "-y"]) == 0
    assert "application menu entry repaired" in capsys.readouterr().out
    assert Cli().main(["where"]) == 0
    assert f"application menu entry: {entry.path}: ok" in capsys.readouterr().out
    program.unlink()
    assert "program missing" in entry.problems()[-1]


def test_gui_install_and_remove_remember_the_wish(tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """``--install-desktop`` sets the wish, ``--remove-desktop`` clears it.

    Args:
        tmp_path: Temporary directory (fixture).
        monkeypatch: Patcher (fixture).
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    monkeypatch.setattr("sys.stdout", io.StringIO())
    assert Cli().main(["gui", "--install-desktop"]) == 0
    assert Config().get("gui", "desktop_entry") == "yes"
    assert Cli().main(["gui", "--remove-desktop"]) == 0
    assert Config().get("gui", "desktop_entry") == "no"
