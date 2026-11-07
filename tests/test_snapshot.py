"""Unit and integration tests for mediatools.snapshot."""
import logging
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from conftest import make_file, needs_rsync
from mediatools.cli import main
from mediatools.core import Config, Prompt
from mediatools.snapshot import (LevelSchedule, RsnapshotConf, SnapshotSettings, Snapshotter,
                                 SyncProbe, SystemdTrigger)
from mediatools.snapshot import trigger as trigger_module
from mediatools.sync.fscheck import FsCheck

LOG = logging.getLogger("test")
needs_rsnapshot = pytest.mark.skipif(shutil.which("rsnapshot") is None,
                                     reason="rsnapshot not installed")


def settings_for(datadir: Path, source: Path, disk: Path, extra: str = "") -> SnapshotSettings:
    """Write a snapshot configuration and return the validated settings.

    Args:
        datadir: Test data directory (``MEDIATOOLS_HOME``).
        source: Directory to back up.
        disk: Directory standing in for the backup disk.
        extra: Additional lines for the ``[snapshot]`` section.

    Returns:
        Settings read through :class:`Config`.
    """
    os.makedirs(datadir, exist_ok=True)
    (datadir / Config.FILE).write_text(
        f"[snapshot]\nsource = {source}\ndisk = {disk}\n{extra}\n", encoding="utf-8")
    return SnapshotSettings.from_config(Config())


def snapshotter(settings: SnapshotSettings, tmp_path: Path, answer: str = "y",
                assume_yes: bool = False) -> Snapshotter:
    """Build a Snapshotter whose disk is a plain directory.

    Args:
        settings: Validated settings.
        tmp_path: Temporary directory (for the fake mount table).
        answer: Answer given to every question.
        assume_yes: Behave like ``-y``.

    Returns:
        The configured object.
    """
    mounts = tmp_path / "mounts"
    mounts.write_text(f"/dev/test {settings.disk} ext4 rw 0 0\n", encoding="utf-8")
    return Snapshotter(settings, LOG, Prompt(assume_yes, lambda _q: answer),
                       fscheck=FsCheck(str(mounts)), ismount=lambda _p: True)


def collection(tmp_path: Path) -> Path:
    """Create a small source tree with a flat hard link.

    Args:
        tmp_path: Temporary directory.

    Returns:
        The source directory.
    """
    source = tmp_path / "home/u/coll"
    for name in ("a", "b", "c"):
        make_file(source / f"unit/posts/{name}.mp4", name.encode() * 1000)
    os.makedirs(source / "unit/_flat")
    os.link(source / "unit/posts/a.mp4", source / "unit/_flat/posts__a.mp4")
    return source


# -- settings, probe, schedule ----------------------------------------------
def test_settings_explain_missing_values() -> None:
    """Missing source/disk produce instructions instead of a traceback."""
    with pytest.raises(SystemExit, match="lsblk"):
        SnapshotSettings.from_config(Config())


def test_settings_levels_and_mirror(datadir: Path, tmp_path: Path) -> None:
    """Levels with retain 0 are dropped; the mirror follows --relative.

    Args:
        datadir: Test data directory (fixture).
        tmp_path: Temporary directory (fixture).
    """
    settings = settings_for(datadir, tmp_path / "src", tmp_path / "disk", "retain_delta = 0")
    assert [level for level, _n in settings.retain] == ["alpha", "beta", "gamma"]
    assert settings.root == str(tmp_path / "disk/rsnapshot")
    assert settings.mirror("/x/.sync") == "/x/.sync/localhost" + str(tmp_path / "src")
    with pytest.raises(SystemExit, match="retain_alpha"):
        settings_for(datadir, tmp_path / "src", tmp_path / "disk", "retain_alpha = 0")
    with pytest.raises(SystemExit, match="relative"):
        settings_for(datadir, tmp_path / "src", tmp_path / "disk", "root = /abs")


def test_probe_parse_and_plausibility() -> None:
    """Itemized output and statistics are evaluated."""
    output = ("*deleting   u/old.mp4\n>f+++++++++ u/new.mp4\n>f.st...... u/changed.mp4\n"
              "Total file size: 1000 bytes\nTotal transferred file size: 200 bytes\n")
    result = SyncProbe.parse(output)
    assert result == {"deleted": ["u/old.mp4"], "new": 1, "changed": 1,
                      "total_bytes": 1000, "transfer_bytes": 200}
    assert SyncProbe.looks_like_copy(result)
    assert not SyncProbe.looks_like_copy({**result, "transfer_bytes": 900})


def test_schedule_due_and_record(datadir: Path, tmp_path: Path) -> None:
    """Higher levels are due after their period, least frequent first.

    Args:
        datadir: Test data directory (fixture).
        tmp_path: Temporary directory (fixture).
    """
    schedule = LevelSchedule(settings_for(datadir, tmp_path, tmp_path / "d"))
    now = datetime(2026, 11, 3, 12, 0)
    assert schedule.due(now) == ["delta", "gamma", "beta"]
    for level in ("delta", "gamma", "beta"):
        schedule.record(level, now)
    assert not schedule.due(now + timedelta(days=6))
    assert schedule.due(now + timedelta(days=7)) == ["beta"]
    assert schedule.due(now + timedelta(days=30)) == ["gamma", "beta"]


def test_rsnapshot_conf_render(datadir: Path, tmp_path: Path) -> None:
    """The generated file contains the safety-relevant settings.

    Args:
        datadir: Test data directory (fixture).
        tmp_path: Temporary directory (fixture).
    """
    conf = RsnapshotConf(settings_for(datadir, tmp_path / "src", tmp_path / "disk"))
    text = conf.render()
    for line in ("no_create_root\t1", "sync_first\t1", "link_dest\t1", "rsync_short_args\t-aH",
                 "retain\talpha\t7", "exclude\tnewShrinkedFiles/",
                 f"backup\t{tmp_path / 'src'}/\tlocalhost/"):
        assert line in text
    assert "--no-inc-recursive" in text and "--delete" in text


def test_trigger_unit(tmp_path: Path) -> None:
    """The service is bound to the disk's mount unit.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    if shutil.which("systemd-escape") is None:
        pytest.skip("systemd-escape not installed")
    trigger = SystemdTrigger("/media/u/BACKUP", "/opt/mediatools", str(tmp_path))
    assert trigger.mount_unit() == "media-u-BACKUP.mount"
    path = trigger.install()
    text = Path(path).read_text(encoding="utf-8")
    assert "WantedBy=media-u-BACKUP.mount" in text
    assert "ExecStart=/opt/mediatools snapshot run -y" in text


# -- Snapshotter with real rsnapshot ------------------------------------------
def test_check_requires_mounted_disk(datadir: Path, tmp_path: Path) -> None:
    """An unmounted disk stops every action.

    Args:
        datadir: Test data directory (fixture).
        tmp_path: Temporary directory (fixture).
    """
    settings = settings_for(datadir, collection(tmp_path), tmp_path / "disk")
    os.makedirs(settings.disk)
    snap = Snapshotter(settings, LOG, Prompt(True), ismount=lambda _p: False)
    assert not snap.check()
    assert snap.run() == 1


@needs_rsync
@needs_rsnapshot
def test_adopt_refuses_wrong_directory(datadir: Path, tmp_path: Path) -> None:
    """A directory that is not a copy of the source is not adopted.

    Args:
        datadir: Test data directory (fixture).
        tmp_path: Temporary directory (fixture).
    """
    settings = settings_for(datadir, collection(tmp_path), tmp_path / "disk")
    make_file(tmp_path / "disk/other/x.bin", b"z" * 50000)
    snap = snapshotter(settings, tmp_path)
    assert snap.adopt(str(tmp_path / "disk/other")) == 1
    assert (tmp_path / "disk/other/x.bin").exists()
    assert not os.path.exists(settings.root)


@needs_rsync
@needs_rsnapshot
def test_adopt_then_run_keeps_removed_files(datadir: Path, tmp_path: Path) -> None:
    """Adoption renames without copying; removed files survive in stand0.

    Args:
        datadir: Test data directory (fixture).
        tmp_path: Temporary directory (fixture).
    """
    source = collection(tmp_path)
    settings = settings_for(datadir, source, tmp_path / "disk", "max_deletions = 1")
    shutil.copytree(source, tmp_path / "disk/mirror", symlinks=True)
    os.remove(tmp_path / "disk/mirror/unit/_flat/posts__a.mp4")
    os.link(tmp_path / "disk/mirror/unit/posts/a.mp4",
            tmp_path / "disk/mirror/unit/_flat/posts__a.mp4")
    make_file(tmp_path / "disk/mirror/unit/only_here.bin", b"keep me")
    os.remove(source / "unit/posts/c.mp4")
    make_file(source / "unit/posts/d.mp4", b"d" * 1000)

    snap = snapshotter(settings, tmp_path)
    inode = os.stat(tmp_path / "disk/mirror/unit/posts/a.mp4").st_ino
    assert snap.adopt(str(tmp_path / "disk/mirror")) == 0
    alpha0 = Path(settings.mirror(os.path.join(settings.root, "alpha.0")))
    stand0 = Path(settings.mirror(os.path.join(settings.root, "stand0")))
    assert os.stat(alpha0 / "unit/posts/a.mp4").st_ino == inode          # renamed, not copied
    assert os.stat(stand0 / "unit/posts/a.mp4").st_ino == inode          # hard-link copy

    # two files would leave the newest snapshot, limit is 1: -y aborts
    assert snapshotter(settings, tmp_path, assume_yes=True).run() == 1
    assert snapshotter(settings, tmp_path, answer="y").run() == 0
    newest = Path(settings.mirror(os.path.join(settings.root, "alpha.0")))
    assert sorted(os.listdir(newest / "unit/posts")) == ["a.mp4", "b.mp4", "d.mp4"]
    assert not (newest / "unit/only_here.bin").exists()
    assert (stand0 / "unit/only_here.bin").read_bytes() == b"keep me"
    assert (stand0 / "unit/posts/c.mp4").exists()
    assert os.stat(newest / "unit/posts/a.mp4").st_ino == inode          # unchanged: shared
    assert os.path.samefile(newest / "unit/_flat/posts__a.mp4", newest / "unit/posts/a.mp4")
    assert snap.status() == 0


@needs_rsync
@needs_rsnapshot
def test_init_probe_report_and_rotation(datadir: Path, tmp_path: Path) -> None:
    """Fresh start: guidance, init, first full run, due beta rotation.

    Args:
        datadir: Test data directory (fixture).
        tmp_path: Temporary directory (fixture).
    """
    source = collection(tmp_path)
    settings = settings_for(datadir, source, tmp_path / "disk",
                            "retain_alpha = 2\nretain_beta = 2\nretain_gamma = 0\n"
                            "retain_delta = 0")
    os.makedirs(settings.disk)
    snap = snapshotter(settings, tmp_path)
    assert snap.probe_report()                       # root missing: next step explained
    assert snap.run() == 1                           # no root yet
    assert snap.init() == 0
    assert snap.init() == 1                          # only once
    assert snap.probe_report()                       # root exists, no snapshot yet
    now = datetime(2026, 11, 3, 12, 0)
    for day in range(3):
        assert snap.run(now=now + timedelta(days=day)) == 0
    names = sorted(os.listdir(settings.root))
    assert "beta.0" in names and "alpha.0" in names
    # beta needs alpha.1, which exists from the third run on
    assert LevelSchedule(settings).load() == {"beta": "2026-11-05T12:00:00"}
    assert snap.probe_report()                       # newest snapshot matches the source
    assert snap.run(dry_run=True) == 0


@needs_rsync
@needs_rsnapshot
def test_adopt_preconditions(datadir: Path, tmp_path: Path) -> None:
    """Adoption needs an existing directory and an empty snapshot root.

    Args:
        datadir: Test data directory (fixture).
        tmp_path: Temporary directory (fixture).
    """
    settings = settings_for(datadir, collection(tmp_path), tmp_path / "disk")
    make_file(tmp_path / "disk/rsnapshot/alpha.0/x", b"1")
    make_file(tmp_path / "disk/mirror/x", b"1")
    snap = snapshotter(settings, tmp_path)
    assert snap.adopt(str(tmp_path / "disk/missing")) == 1
    assert snap.adopt(str(tmp_path / "disk/mirror")) == 1           # root not empty
    assert snapshotter(settings, tmp_path, answer="n").run(dry_run=True) == 0


def test_cli_snapshot_without_configuration() -> None:
    """Without configuration the CLI explains what to set."""
    with pytest.raises(SystemExit, match=r"\[snapshot\] source, disk not set"):
        main(["snapshot", "check"])


def test_cli_snapshot_unmounted_disk(datadir: Path, tmp_path: Path,
                                     capsys: pytest.CaptureFixture) -> None:
    """A plain directory is rejected as disk; status reports the missing root.

    Args:
        datadir: Test data directory (fixture).
        tmp_path: Temporary directory (fixture).
        capsys: Output capture (fixture).
    """
    settings_for(datadir, collection(tmp_path), tmp_path / "disk")
    os.makedirs(tmp_path / "disk")
    assert main(["snapshot", "check"]) == 1
    assert "not a mount point" in capsys.readouterr().out
    assert main(["snapshot", "status"]) == 1
    assert main(["snapshot", "run", "-y"]) == 1
    with pytest.raises(SystemExit, match="adopt needs"):
        main(["snapshot", "adopt"])
    assert main(["config"]) == 0
    assert "[snapshot]" in capsys.readouterr().out
    assert main(["config", "--path"]) == 0


def test_trigger_without_systemctl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without systemctl the mount unit counts as unknown.

    Args:
        tmp_path: Temporary directory (fixture).
        monkeypatch: Patcher (fixture).
    """
    monkeypatch.setattr(trigger_module.shutil, "which", lambda tool: None)
    trigger = SystemdTrigger(str(tmp_path), "/usr/bin/mediatools")
    assert not trigger.mount_unit_known()
    with pytest.raises(SystemExit, match="systemd-escape"):
        trigger.mount_unit()
