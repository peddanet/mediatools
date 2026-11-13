"""1.3.0 GUI: the parts without Tk (forms from the parser, runner, monitor, files)."""
import argparse
import os
import sys
import time
from pathlib import Path

import pytest

from mediatools.cli import Cli
from mediatools.core.runcontrol import RunControl
from mediatools.gui.catalog import CommandCatalog
from mediatools.gui.commandspec import CommandSpec
from mediatools.gui.desktopentry import DesktopEntry
from mediatools.gui.formstore import FormStore
from mediatools.gui.jobrunner import JobRunner
from mediatools.gui.launcher import GuiLauncher
from mediatools.gui.opener import Opener
from mediatools.gui.optionspec import OptionSpec
from mediatools.gui.shrinkmonitor import ShrinkMonitor


def catalog() -> CommandCatalog:
    """Return the catalog of the real command line.

    Returns:
        The catalog.
    """
    return CommandCatalog(Cli().parser)


def test_option_words_for_every_kind() -> None:
    """Each kind of field turns into the right command-line words."""
    flag = OptionSpec("keep", ["--keep"], kind="flag")
    assert flag.to_argv(True) == ["--keep"] and not flag.to_argv(False)
    assert flag.initial() is False
    many = OptionSpec("set", ["--set"], kind="list")
    assert many.to_argv(["a.b=1", " ", "c.d=2"]) == ["--set", "a.b=1", "--set", "c.d=2"]
    assert many.to_argv("x.y=3\n") == ["--set", "x.y=3"] and many.initial() == []
    number = OptionSpec("crf", ["--crf"], kind="int", default=28)
    assert number.to_argv("26") == ["--crf", "26"] and number.initial() == "28"
    with pytest.raises(ValueError):
        number.to_argv("abc")
    with pytest.raises(ValueError):
        OptionSpec("s", ["--s"], kind="float").to_argv("x")
    assert OptionSpec("s", ["--s"], kind="float").to_argv("1.5") == ["--s", "1.5"]
    positional = OptionSpec("path", kind="text", path=True)
    assert positional.positional and positional.label == "path"
    assert positional.to_argv(" /data ") == ["/data"] and not positional.to_argv("")
    assert not positional.to_argv(None) and positional.initial() == ""
    assert OptionSpec("yes", ["-y", "--yes"], kind="flag").label == "--yes"
    assert OptionSpec("q", ["-q"], kind="flag").label == "-q"


def test_command_line_order_checks_and_quoting() -> None:
    """Positionals first, required ones enforced, the line is quoted."""
    spec = CommandSpec("rename-db", options=[
        OptionSpec("dry_run", ["--dry-run"], kind="flag"),
        OptionSpec("old", required=True), OptionSpec("path"),
        OptionSpec("n", ["--n"], kind="int")])
    assert spec.argv({"dry_run": True, "old": "a b.db", "path": "/x"}) == \
        ["rename-db", "a b.db", "/x", "--dry-run"]
    assert spec.command_line({"old": "a b.db"}) == "mediatools rename-db 'a b.db'"
    with pytest.raises(ValueError, match="old is required"):
        spec.argv({})
    with pytest.raises(ValueError, match="--n: not a number"):
        spec.argv({"old": "a", "n": "x"})
    assert spec.option("--dry-run") is not None and spec.option("--nope") is None
    assert spec.changes_data({"old": "a"}) and not spec.changes_data({"dry_run": True})
    assert not CommandSpec("progress").changes_data({})


def test_catalog_covers_every_command_and_option() -> None:
    """Every subcommand and visible option of the command line is in the GUI."""
    commands = {spec.name: spec for spec in catalog().commands()}
    assert set(commands) == {"shrink", "reload", "status", "stop", "update", "progress",
                             "triage", "flatten", "sync", "rename-db", "config", "snapshot",
                             "savings"}
    shrink = commands["shrink"]
    assert shrink.option("--continue-log") is None            # hidden option
    assert shrink.option("--order").kind == "choice"            # type: ignore[union-attr]
    assert "largest" in shrink.option("--order").choices        # type: ignore[union-attr]
    assert shrink.option("path").path and shrink.option("--crf").kind == "int"  # type: ignore
    assert commands["config"].option("--set").kind == "list"    # type: ignore[union-attr]
    assert not commands["config"].option("--path").path         # type: ignore[union-attr]
    assert commands["snapshot"].option("action").required       # type: ignore[union-attr]
    assert commands["flatten"].option("--undo").path            # type: ignore[union-attr]
    assert not commands["rename-db"].option("old").path         # type: ignore[union-attr]
    assert "HEVC" in shrink.help
    for spec in commands.values():                              # every line parses
        args = spec.argv({o.dest: ("check" if o.required else o.initial())
                          for o in spec.options})
        Cli().parser.parse_args(args)
    assert catalog().command("sync").name == "sync"
    with pytest.raises(KeyError):
        catalog().command("attach")


def test_catalog_needs_subcommands() -> None:
    """A parser without subcommands is rejected."""
    with pytest.raises(ValueError):
        CommandCatalog(argparse.ArgumentParser()).commands()


def runner(code: str) -> JobRunner:
    """Return a runner whose program is a Python snippet.

    Args:
        code: Python source.

    Returns:
        The runner.
    """
    return JobRunner([sys.executable, "-c", code])


def collect(job: JobRunner, until: str = "", seconds: float = 20.0) -> tuple[str, int | None]:
    """Poll until text appears or the command ends.

    Args:
        job: The runner.
        until: Text to wait for; empty waits for the end.
        seconds: Time limit.

    Returns:
        Output so far and the exit code (None while running).
    """
    text, code, end = "", None, time.monotonic() + seconds
    while time.monotonic() < end:
        new, code = job.poll()
        text += new
        if code is not None or (until and until in text):
            break
        time.sleep(0.02)
    return text, code


def test_runner_streams_questions_and_answers() -> None:
    """A question without line end arrives at once; the answer goes in."""
    job = runner("import sys; a = input('Continue? [y/N]: '); print('got', a); "
                 "print('ümlaut'); sys.exit(3)")
    assert not job.running and job.wait() is None
    job.start([])
    assert job.running
    with pytest.raises(RuntimeError):
        job.start([])
    text, code = collect(job, "[y/N]: ")
    assert text.endswith("[y/N]: ") and code is None
    job.send("y")
    text, code = collect(job)
    assert "got y" in text and "ümlaut" in text and code == 3
    with pytest.raises(RuntimeError):
        job.send("again")
    assert job.cancel() == "no command is running"


def test_runner_cancel_interrupts_then_terminates() -> None:
    """First cancel sends SIGINT (caught here), the second SIGTERM."""
    job = runner("import signal, time, sys\n"
                 "signal.signal(signal.SIGINT, lambda *a: print('int', flush=True))\n"
                 "print('ready', flush=True)\n"
                 "while True: time.sleep(0.05)\n")
    job.start(["x"])
    text = collect(job, "ready")[0]
    assert job.cancel().startswith("interrupt sent")
    text += collect(job, "int")[0]
    assert job.cancel() == "terminate signal sent"
    more, code = collect(job)
    assert "int" in text + more and code == -15
    assert job.wait(5) == -15


def test_runner_cancel_after_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """A child that ended between check and signal is reported.

    Args:
        monkeypatch: Patcher (fixture).
    """
    job = runner("import time; time.sleep(30)")
    job.start([])

    def gone(_pid: int, _sig: int) -> None:
        """Act like a child that has just ended.

        Raises:
            ProcessLookupError: Always.
        """
        raise ProcessLookupError

    real = os.killpg
    monkeypatch.setattr("mediatools.gui.jobrunner.os.killpg", gone)
    assert job.cancel() == "the command has already ended"
    monkeypatch.setattr("mediatools.gui.jobrunner.os.killpg", real)
    job.cancel()
    job.cancel()
    assert job.wait(10) is not None


def test_runner_runs_mediatools_with_this_interpreter() -> None:
    """The default program is this interpreter with ``-m mediatools``."""
    job = JobRunner()
    assert job.program == [sys.executable, "-m", "mediatools"]
    job.start(["--version"])
    text, code = collect(job)
    assert code == 0 and text.startswith("mediatools ")


def write_run(datadir: Path, status: str | None, log_lines: int = 3) -> str:
    """Pretend a shrink runs: PID file (this process), log, status file.

    Args:
        datadir: Data directory.
        status: Content of the status file, or None for none.
        log_lines: Number of log lines.

    Returns:
        Path of the log.
    """
    control = RunControl("shrink")
    log = datadir / "logs" / "shrink.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("".join(f"2026-11-12 10:00:{i:02d} INFO  line {i}\n"
                           for i in range(log_lines)), encoding="utf-8")
    Path(control.pid_file).write_text(f"{os.getpid()}\n{log}\n", encoding="utf-8")
    if status is not None:
        Path(control.status_file).write_text(status, encoding="utf-8")
    return str(log)


def test_monitor_reads_a_running_shrink(datadir: Path) -> None:
    """PID, progress lines, percentage, log and overall space are read.

    Args:
        datadir: Data directory (fixture).
    """
    log = write_run(datadir, "total [##......]  12.5 % | 1/8 files\nfile 3 %\nspace x\n")
    monitor = ShrinkMonitor()
    state = monitor.read()
    assert state.pid == os.getpid() and state.log_path == log
    assert state.progress == ["total [##......]  12.5 % | 1/8 files", "file 3 %", "space x"]
    assert state.percent == 12.5 and state.report[0].startswith("shrink: running")
    assert state.overall.startswith("space  session: 0.0 GiB read")
    Path(monitor.control.status_file).write_text("starting\n", encoding="utf-8")
    assert monitor.read().percent is None
    os.remove(monitor.control.status_file)
    assert not monitor.read().progress
    assert monitor.stop().startswith("stop requested")
    assert monitor.reload().startswith("reload requested")


def test_monitor_without_run() -> None:
    """Without a run only the report and the overall space are filled."""
    monitor = ShrinkMonitor()
    state = monitor.read()
    assert state.pid is None and not state.progress and state.log_path is None
    assert state.report[0] == "shrink: not running"
    assert state.overall.startswith("space  last session: 0.0 GiB read")
    assert monitor.stop().startswith("no shrink run is active")
    assert monitor.reload().startswith("no shrink run is active")


def test_tail_of_large_and_missing_logs(tmp_path: Path) -> None:
    """The tail drops the cut first line and limits the number of lines.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    log = tmp_path / "big.log"
    log.write_text("".join(f"line {i:05d}\n" for i in range(20000)), encoding="utf-8")
    lines = ShrinkMonitor.tail(str(log), lines=5, chunk=1000)
    assert lines == [f"line {i:05d}" for i in range(19995, 20000)]
    small = ShrinkMonitor.tail(str(log), lines=1000, chunk=1000)
    assert small[0].startswith("line ") and len(small[0]) == 10
    assert not ShrinkMonitor.tail(None) and not ShrinkMonitor.tail(str(tmp_path / "no.log"))


def test_form_store_remembers_values(tmp_path: Path) -> None:
    """Values survive; a damaged or foreign file is ignored.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    store = FormStore(str(tmp_path / "gui.json"))
    assert not store.load("shrink")
    store.save("shrink", {"crf": "26"})
    store.save("sync", {"reverse": True})
    assert FormStore(store.path).load("shrink") == {"crf": "26"}
    Path(store.path).write_text("[1]", encoding="utf-8")
    assert not store.load("shrink")
    Path(store.path).write_text('{"shrink": 5}', encoding="utf-8")
    assert not store.load("shrink")
    Path(store.path).write_text("{bad", encoding="utf-8")
    assert not store.load("shrink")
    assert FormStore().path.endswith(os.path.join("state", "gui.json"))


def test_desktop_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The entry starts the program with ``gui``; install and remove work.

    Args:
        tmp_path: Temporary directory (fixture).
        monkeypatch: Environment patcher (fixture).
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    entry = DesktopEntry('/home/x/scripts/media"tools')
    assert entry.path == str(tmp_path / "share" / "applications" / "mediatools.desktop")
    assert 'Exec="/home/x/scripts/media\\"tools" gui' in entry.text()
    assert "Icon=mediatools\n" in entry.text() and "StartupWMClass=Mediatools\n" in entry.text()
    assert entry.install() == entry.path and "Type=Application" in \
        Path(entry.path).read_text(encoding="utf-8")
    icons = tmp_path / "share" / "icons" / "hicolor"
    svg = icons / "scalable" / "apps" / "mediatools.svg"
    png = icons / "256x256" / "apps" / "mediatools.png"
    assert svg.read_text(encoding="utf-8").startswith("<svg")
    assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert entry.remove() and not svg.exists() and not png.exists()
    assert not entry.remove()


def test_opener(tmp_path: Path) -> None:
    """The opener starts its program; without one it says so.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    marker = tmp_path / "opened"
    script = tmp_path / "open.sh"
    script.write_text(f'#!/bin/sh\necho "$1" > "{marker}"\n', encoding="utf-8")
    script.chmod(0o755)
    assert Opener(str(script)).open("/data") == "opened /data"
    end = time.monotonic() + 10
    while not marker.exists() and time.monotonic() < end:
        time.sleep(0.02)
    assert marker.read_text().strip() == "/data"
    missing = Opener()
    missing.program = None
    assert missing.open("/x").startswith("xdg-open is not installed")


def test_launcher_checks_display_and_tk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                        capsys: pytest.CaptureFixture[str]) -> None:
    """Missing display or Tk give a remedy instead of a traceback.

    Args:
        tmp_path: Temporary directory (fixture).
        monkeypatch: Environment patcher (fixture).
        capsys: Output capture (fixture).
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    parser = Cli().parser
    launcher = GuiLauncher(parser, "/x/mediatools", {})
    assert "DISPLAY" in str(launcher.problem())
    assert launcher.run() == 2 and "no graphical session" in capsys.readouterr().out
    real = __import__("importlib").import_module

    def no_tk(name: str) -> object:
        """Fail for tkinter.

        Args:
            name: Module name.

        Returns:
            The module.

        Raises:
            ImportError: For tkinter.
        """
        if name == "tkinter":
            raise ImportError(name)
        return real(name)

    monkeypatch.setattr("mediatools.gui.launcher.importlib.import_module", no_tk)
    wayland = GuiLauncher(parser, "/x/mediatools", {"WAYLAND_DISPLAY": "wayland-0"})
    assert "sudo apt install python3-tk" in str(wayland.problem())
    assert wayland.run(install_desktop=True) == 0
    assert "entry written" in capsys.readouterr().out
    assert wayland.run(remove_desktop=True) == 0 and wayland.run(remove_desktop=True) == 0
    assert "there was no entry" in capsys.readouterr().out


def test_cli_gui_menu_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``mediatools gui --install-desktop`` writes the entry without a window.

    Args:
        tmp_path: Temporary directory (fixture).
        monkeypatch: Environment patcher (fixture).
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert Cli().main(["gui", "--install-desktop"]) == 0
    entry = tmp_path / "share" / "applications" / "mediatools.desktop"
    assert "gui" in entry.read_text(encoding="utf-8")
    (tmp_path / "home" / "scripts").mkdir(parents=True)
    (tmp_path / "home" / "scripts" / "mediatools").write_text("", encoding="utf-8")
    assert Cli().main(["gui", "--install-desktop"]) == 0
    assert str(tmp_path / "home" / "scripts" / "mediatools") in entry.read_text()
    assert Cli().main(["gui", "--remove-desktop"]) == 0 and not entry.exists()
