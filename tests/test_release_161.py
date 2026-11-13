"""Release 1.6.1: scheme of the desktop, accent colour, libx265 preset."""
import subprocess
import tkinter as tk

import pytest

from test_gui_tk import fixture_root  # pylint: disable=unused-import
from test_release_160_gui import FakeLook
from mediatools.cli import Cli
from mediatools.core.config import Config
from mediatools.gui.appearance import Appearance
from mediatools.gui.systemlook import SystemLook
from mediatools.shrink.encoder import HevcEncoder
from mediatools.shrink.jobregistry import JobEntry, JobRegistry
from mediatools.shrink.jobspec import JobSpec


class FakeBus:
    """Runner double for ``gdbus``: answers per method and key."""

    def __init__(self, answers: dict[tuple[str, str], str]) -> None:
        """Store the answers.

        Args:
            answers: Output by (method, key); missing pairs fail.
        """
        self.answers = answers
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess:
        """Answer one call.

        Args:
            cmd: Command line.
            **_kwargs: Ignored options of :func:`subprocess.run`.

        Returns:
            The result.
        """
        self.calls.append(cmd)
        key = (cmd[cmd.index("--method") + 1].rsplit(".", 1)[1], cmd[-1])
        text = self.answers.get(key)
        return subprocess.CompletedProcess(cmd, 0 if text else 1, text or "", "")


def test_portal_answers_are_parsed() -> None:
    """ReadOne first, Read as fallback; accent as #rrggbb; bad values ignored."""
    bus = FakeBus({("ReadOne", "color-scheme"): "(<uint32 1>,)",
                   ("Read", "accent-color"): "(<<(0.2, 0.4, 1.0)>>,)"})
    look = SystemLook(bus, gdbus="/usr/bin/gdbus")
    assert look.scheme() == "dark" and look.accent() == "#3366ff"
    assert [call[-3].rsplit(".", 1)[1] for call in bus.calls] == ["ReadOne", "ReadOne", "Read"]
    assert bus.calls[0][:3] == ["/usr/bin/gdbus", "call", "--session"]
    light = SystemLook(FakeBus({("ReadOne", "color-scheme"): "(<uint32 2>,)",
                                ("ReadOne", "accent-color"): "(<(1.5, 0.0, 0.0)>,)"}),
                       gdbus="gdbus")
    assert light.scheme() == "light" and light.accent() is None
    none = SystemLook(FakeBus({("ReadOne", "color-scheme"): "(<uint32 0>,)"}), gdbus="gdbus")
    assert none.scheme() is None and none.accent() is None


def test_no_gdbus_or_a_hanging_bus() -> None:
    """Without gdbus, or when the call times out, nothing is known."""
    missing = SystemLook(FakeBus({}), gdbus="gdbus")
    missing.gdbus = None
    assert missing.read("color-scheme") is None and missing.scheme() is None

    def hang(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess:
        raise subprocess.TimeoutExpired(cmd, 2.0)

    assert SystemLook(hang, gdbus="gdbus").scheme() is None


def test_auto_follows_the_desktop(root: tk.Tk) -> None:
    """``auto`` is the default, takes scheme and accent and follows changes.

    Args:
        root: Main window (fixture).
    """
    desktop = FakeLook("light", "#3366ff")
    look = Appearance(root, Config(), desktop)
    look.apply()
    assert look.theme == "auto" and look.scheme() == "light"
    assert look.colors["accent"] == "#3366ff" and look.colors["accent_fg"] == "#ffffff"
    desktop.values = ["dark", "#ffd700"]
    look.probe()
    assert look.take_probe() and look.scheme() == "dark"
    assert look.colors["accent_fg"] == "#0d1117"                 # light accent: dark text
    assert not look.take_probe()                                 # nothing new
    desktop.values = [None, None]
    look.probe()
    assert look.take_probe() and look.scheme() == Appearance.FALLBACK
    assert look.set_theme("light") == "light" and Config().get("gui", "theme") == "light"
    look.probe()
    assert not look.take_probe()                                 # fixed scheme ignores the desktop
    assert look.set_theme("auto") == Appearance.FALLBACK and look.choice.get() == "auto"
    with pytest.raises(ValueError):
        look.set_theme("blue")
    look.watch(60_000)
    assert Appearance.mix("#000000", "#ffffff", 0.5) == "#808080"
    assert Appearance.luminance("#ffffff") == pytest.approx(1.0)


def test_preset_reaches_ffmpeg_jobs_and_form() -> None:
    """The preset is an ffmpeg option, a job setting and a form value."""
    encoder = HevcEncoder(preset="faster", niceness=0)
    cmd = encoder.command("in.mp4", "out.mp4")
    assert cmd[cmd.index("-preset") + 1] == "faster" and encoder.clone().preset == "faster"
    with pytest.raises(ValueError):
        HevcEncoder(preset="warp")
    entry = JobEntry(JobSpec("/m/c"), {"crf": 28, "preset": "faster", "jobs": 2})
    assert entry.argv() == ["shrink", "/m/c", "--preset", "faster", "--jobs", "2"]
    assert "crf 28 faster" in JobRegistry.line(entry)
    assert Cli().parser.parse_args(["shrink", "--preset", "fast"]).preset == "fast"


def test_preset_from_the_configuration() -> None:
    """``[shrink] preset`` applies without the option; a wrong value stops."""
    config = Config()
    assert Cli._preset(None, config) == "medium"                # pylint: disable=protected-access
    config.set_value("shrink", "preset", "faster")
    assert Cli._preset(None, config) == "faster"                # pylint: disable=protected-access
    assert Cli._preset("slow", config) == "slow"                # pylint: disable=protected-access
    config.set_value("shrink", "preset", "warp")
    with pytest.raises(SystemExit):
        Cli._preset(None, config)                               # pylint: disable=protected-access
