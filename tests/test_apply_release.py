"""mediatools update (apply-release.sh, 1.14.0): newer release, then patch archives.

GitHub is replaced by a bare repository and the fake ``gh`` of
``test_release_tools``; the clone carries the real ``tools/release.sh`` and
``tools/same-code.sh`` and a fake ``install.sh`` that writes a stand-in
program to ``~/.local/bin/mediatools``.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
import time
from pathlib import Path

import pytest

from test_release_tools import FAKE_GH
from mediatools.cli import Cli
from mediatools.update import ReleaseUpdater

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "src" / "mediatools" / "update" / "apply-release.sh"
FAKE_INSTALL = """#!/bin/sh
mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/mediatools" <<EOT
#!/bin/sh
if [ "\\$1" = --version ]; then echo "mediatools ${1#v}"; else echo "\\$*" >> "$HOME/calls"; fi
EOT
chmod +x "$HOME/.local/bin/mediatools"
echo "install $1" >> "$HOME/calls"
"""


def git(*args: str, cwd: Path) -> str:
    """Run git with a fixed identity.

    Args:
        *args: git arguments.
        cwd: Working directory.

    Returns:
        Standard output, stripped.
    """
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                           *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


@pytest.fixture(name="setup")
def fixture_setup(tmp_path: Path) -> dict[str, Path]:
    """A clone at release 1.2.5 (main, develop, tag) on a bare repository (origin), and Downloads.

    Args:
        tmp_path: Temporary directory (fixture).

    Returns:
        Paths ``home``, ``repo``, ``origin``, ``downloads``, ``bin`` and ``gh``.
    """
    home, repo, origin = tmp_path / "home", tmp_path / "repo", tmp_path / "origin.git"
    paths = {"home": home, "repo": repo, "origin": origin, "downloads": home / "Downloads",
             "bin": tmp_path / "bin", "gh": tmp_path / "gh"}
    for key in ("downloads", "bin", "gh"):
        paths[key].mkdir(parents=True)
    (paths["bin"] / "gh").write_text(FAKE_GH, encoding="utf-8")
    (paths["bin"] / "gh").chmod(0o755)
    git("init", "-q", "--bare", "-b", "main", str(origin), cwd=tmp_path)
    git("clone", "-q", str(origin), str(repo), cwd=tmp_path)
    (repo / "src" / "mediatools").mkdir(parents=True)
    (repo / "src" / "mediatools" / "__init__.py").write_text('__version__ = "1.2.5"\n')
    (repo / "CHANGELOG.md").write_text("# Changelog\n\n## [Unreleased]\n\n## [1.2.5] - x\n")
    (repo / "pyproject.toml").write_text('[project]\nname = "mediatools"\n'
                                         'version = { attr = "mediatools.__version__" }\n')
    (repo / "tools").mkdir()
    for name in ("release.sh", "same-code.sh"):
        shutil.copy2(ROOT / "tools" / name, repo / "tools" / name)
    (repo / "install.sh").write_text(FAKE_INSTALL)
    (repo / "install.sh").chmod(0o755)
    for command in (["switch", "-q", "-c", "main"], ["add", "-A"], ["commit", "-qm", "1.2.5"],
                    ["tag", "-a", "v1.2.5", "-m", "x"], ["branch", "develop"],
                    ["push", "-q", "origin", "main", "develop", "v1.2.5"]):
        git(*command, cwd=repo)
    return paths


def environment(setup: dict[str, Path]) -> dict[str, str]:
    """Build the environment of an update run.

    Args:
        setup: Paths from the fixture.

    Returns:
        Environment variables.
    """
    env = dict(os.environ, HOME=str(setup["home"]), APPLY_RELEASE_ALLOW_ROOT="1",
               MEDIATOOLS_VERSION="1.2.5", CHECK_CMD="true", RELEASE_WAIT="0",
               FAKE_GH_DIR=str(setup["gh"]), FAKE_GH_ORIGIN=str(setup["origin"]),
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid",
               PATH=f"{setup['bin']}{os.pathsep}{os.environ['PATH']}")
    for name in ("APPLY_RELEASE_ORIGIN", "APPLY_RELEASE_COPY", "MEDIATOOLS_HOME",
                 "XDG_STATE_HOME", "XDG_DATA_HOME", "MEDIATOOLS_REPO"):
        env.pop(name, None)
    return env


def run(setup: dict[str, Path], *args: str, stdin: str = "",
        check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run the update script against the clone.

    Args:
        setup: Paths from the fixture.
        *args: Script arguments.
        stdin: Answers.
        check: Raise on a non-zero exit code.

    Returns:
        The finished process (stdout holds stdout and stderr: the script tees).
    """
    return subprocess.run(["bash", str(SCRIPT), "--repo", str(setup["repo"]),
                           "--downloads", str(setup["downloads"]), *args],
                          env=environment(setup), input=stdin, capture_output=True,
                          text=True, check=check, timeout=120)


# pylint: disable-next=too-many-arguments,too-many-locals
def archive(setup: dict[str, Path], name: str, release: str, *, start: str = "develop",
            files: tuple[str, ...] = ("new.txt",), guard: str = "tree",
            age: int = 0) -> Path:
    """Build a patch archive like Claude delivers it.

    Args:
        setup: Paths from the fixture.
        name: Feature name (branch ``feature/<name>``).
        release: Value of ``release`` in patch.info.
        start: Commit the feature starts from (in a scratch clone of origin;
            ``develop`` or an earlier feature branch name).
        files: Files the feature adds (content: the file name).
        guard: ``tree`` (base_tree and tree), ``blob`` (blob lines for
            CHANGELOG.md) or ``none``.
        age: Age of the archive file in seconds (order of application).

    Returns:
        Path of the archive in the download folder.
    """
    scratch = setup["home"].parent / "scratch"
    if not scratch.exists():
        git("clone", "-q", str(setup["origin"]), str(scratch), cwd=setup["home"].parent)
    branch = f"feature/{name}"
    base = "origin/develop" if start == "develop" else f"feature/{start}"
    git("switch", "-q", "-c", branch, base, cwd=scratch)
    for item in files:
        (scratch / item).write_text(item + "\n")
    log = scratch / "CHANGELOG.md"
    log.write_text(log.read_text().replace("## [Unreleased]\n", f"## [Unreleased]\n\n- {name}\n"))
    git("add", "-A", cwd=scratch)
    git("commit", "-qm", f"{name}: change", cwd=scratch)
    folder = setup["home"].parent / f"archive-{name}" / f"mediatools-feature-{name}"
    folder.mkdir(parents=True)
    git("format-patch", "-q", "-o", str(folder), f"{base}..{branch}", cwd=scratch)
    lines = [f"branch={branch}", "base=develop", f"release={release}", f"message={name} done"]
    if guard == "tree":
        lines += [f"base_tree={git('rev-parse', base + '^{tree}', cwd=scratch)}",
                  f"tree={git('rev-parse', 'HEAD^{tree}', cwd=scratch)}"]
    elif guard == "blob":
        before = git("rev-parse", f"{base}:CHANGELOG.md", cwd=scratch)
        after = git("rev-parse", "HEAD:CHANGELOG.md", cwd=scratch)
        lines.append(f"blob=CHANGELOG.md {before} {after}")
    (folder / "patch.info").write_text("\n".join(lines) + "\n")
    target = setup["downloads"] / f"mediatools-feature-{name}.tar.gz"
    with tarfile.open(target, "w:gz") as tar:
        tar.add(folder, arcname=folder.name)
    stamp = time.time() - age
    os.utime(target, (stamp, stamp))
    return target


def remote_tags(setup: dict[str, Path]) -> str:
    """List the tags on origin.

    Args:
        setup: Paths from the fixture.

    Returns:
        Output of git ls-remote.
    """
    return git("ls-remote", "--tags", str(setup["origin"]), cwd=setup["repo"])


def test_list_shows_releases_archives_and_stale_copies(setup: dict[str, Path]) -> None:
    """--list names the installed and newest release, pending, stale and refused archives.

    Args:
        setup: Paths from the fixture.
    """
    archive(setup, "x", "minor")
    dl = setup["downloads"]
    shutil.copy2(dl / "mediatools-feature-x.tar.gz", dl / "mediatools-feature-x (1).tar.gz")
    (dl / "mediatools-hotfix-1.2.4-patch.tar.gz").write_text("old")
    (dl / "mediatools-hotfix-1.3.0-patch.tar.gz").write_text("newer, old format")
    out = run(setup, "--list").stdout
    assert "installed release: 1.2.5" in out and "newest release:    v1.2.5 - installed" in out
    assert out.count("pending  feature") == 1
    assert "stale    mediatools-hotfix-1.2.4-patch.tar.gz" in out
    assert "refused  mediatools-hotfix-1.3.0-patch.tar.gz" in out
    assert out.count("stale    mediatools-feature-x") == 1
    assert len(list(dl.iterdir())) == 4                       # --list changes nothing


def test_feature_archive_is_released_without_questions(setup: dict[str, Path]) -> None:
    """-y applies the archive, releases it with its part and moves it to releases.

    Args:
        setup: Paths from the fixture.
    """
    path = archive(setup, "x", "patch")
    out = run(setup, "-y").stdout
    assert "-> v1.2.6 aus develop - x done" in out and "applied: feature/x" in out
    assert "refs/tags/v1.2.6" in remote_tags(setup)
    assert not path.exists()
    assert (setup["home"] / ".local/share/mediatools/releases" / path.name).is_file()
    assert "install v1.2.6" in (setup["home"] / "calls").read_text()


def test_merge_only_archives_chain_until_the_release(setup: dict[str, Path]) -> None:
    """release=none merges only; the next archive built on it is released in the same run.

    Args:
        setup: Paths from the fixture.
    """
    first = archive(setup, "a", "none", age=60)
    second = archive(setup, "b", "minor", start="a", files=("b.txt",), guard="blob")
    out = run(setup, "-y").stdout
    assert "applied: feature/a feature/b" in out
    assert "refs/tags/v1.3.0" in remote_tags(setup) and "v1.2.6" not in remote_tags(setup)
    assert not first.exists() and not second.exists()


def test_question_declined_keeps_the_archive(setup: dict[str, Path]) -> None:
    """Without 'j' nothing is applied and the archive stays.

    Args:
        setup: Paths from the fixture.
    """
    path = archive(setup, "x", "minor")
    out = run(setup, stdin="n\n").stdout
    assert "skipped: feature/x" in out and "no patch applied" in out
    assert path.exists() and "v1.3.0" not in remote_tags(setup)


def test_wrong_base_stops_before_any_change(setup: dict[str, Path]) -> None:
    """A develop that moved since the patch was built stops loudly; nothing is created.

    Args:
        setup: Paths from the fixture.
    """
    path = archive(setup, "x", "minor")
    repo = setup["repo"]
    git("switch", "-q", "develop", cwd=repo)
    (repo / "other.txt").write_text("other\n")
    git("add", "-A", cwd=repo)
    git("commit", "-qm", "develop moved", cwd=repo)
    git("push", "-q", "origin", "develop", cwd=repo)
    result = run(setup, "-y", check=False)
    assert result.returncode != 0 and "STOPP: develop differs from the base" in result.stdout
    assert path.exists()
    assert "feature/x" not in git("branch", "--list", "feature/*", cwd=repo)


def test_patch_that_does_not_apply_leaves_a_clean_clone(setup: dict[str, Path]) -> None:
    """Without hash guards a conflicting patch is aborted; branch removed, clone clean.

    Args:
        setup: Paths from the fixture.
    """
    archive(setup, "x", "minor", files=("same.txt",), guard="none")
    repo = setup["repo"]
    git("switch", "-q", "develop", cwd=repo)
    (repo / "same.txt").write_text("different\n")
    git("add", "-A", cwd=repo)
    git("commit", "-qm", "conflict", cwd=repo)
    git("push", "-q", "origin", "develop", cwd=repo)
    result = run(setup, "-y", check=False)
    assert "STOPP: the patches do not apply onto develop" in result.stdout
    assert not git("status", "--porcelain", cwd=repo)
    assert git("branch", "--show-current", cwd=repo) == "develop"
    assert "feature/x" not in git("branch", "--list", "feature/*", cwd=repo)


def test_newer_release_is_installed_first(setup: dict[str, Path]) -> None:
    """Step 1: a newer tag is installed with the clone's install.sh, then reload is sent.

    Args:
        setup: Paths from the fixture.
    """
    repo = setup["repo"]
    git("tag", "-a", "v1.2.6", "-m", "newer", "main", cwd=repo)
    git("push", "-q", "origin", "v1.2.6", cwd=repo)
    out = run(setup).stdout
    assert "newest release:    v1.2.6 - newer" in out and "no patch applied" in out
    calls = (setup["home"] / "calls").read_text().splitlines()
    assert calls == ["install v1.2.6", "reload"]


def test_help_and_unknown_option(setup: dict[str, Path]) -> None:
    """--help prints the header; an unknown option is refused.

    Args:
        setup: Paths from the fixture.
    """
    assert "install a newer release" in run(setup, "--help").stdout
    result = run(setup, "--bogus", check=False)
    assert result.returncode == 2 and "unknown option" in result.stdout


def test_inherited_variables_never_delete_the_script(setup: dict[str, Path],
                                                     tmp_path: Path) -> None:
    """Variables leaked from an outer run (pre-push hook) must not delete the original.

    Args:
        setup: Paths from the fixture.
        tmp_path: Temporary directory (fixture).
    """
    script = tmp_path / "apply-release.sh"
    script.write_bytes(SCRIPT.read_bytes())
    env = environment(setup) | {"APPLY_RELEASE_ORIGIN": str(script),
                                "APPLY_RELEASE_COPY": "/tmp/other-copy.sh"}
    for args in (["--bogus"], ["--repo", str(setup["repo"]), "--list"]):
        subprocess.run(["bash", str(script), *args], env=env, capture_output=True,
                       text=True, check=False, timeout=60)
        assert script.is_file()


def test_control_variables_are_not_exported(setup: dict[str, Path], tmp_path: Path) -> None:
    """Programs started by the script (git and its hooks) do not see its variables.

    Args:
        setup: Paths from the fixture.
        tmp_path: Temporary directory (fixture).
    """
    real_git = shutil.which("git")
    record = tmp_path / "seen.txt"
    fake = tmp_path / "gitbin" / "git"
    fake.parent.mkdir()
    fake.write_text(f'#!/bin/sh\necho "[$APPLY_RELEASE_ORIGIN][$APPLY_RELEASE_COPY]" >> {record}\n'
                    f'exec {real_git} "$@"\n', encoding="utf-8")
    fake.chmod(0o755)
    env = environment(setup)
    env["PATH"] = f"{fake.parent}{os.pathsep}{env['PATH']}"
    subprocess.run(["bash", str(SCRIPT), "--repo", str(setup["repo"]), "--list"],
                   env=env, capture_output=True, text=True, check=True, timeout=60)
    assert set(record.read_text(encoding="utf-8").splitlines()) == {"[][]"}


def test_update_subcommand_forwards_options(setup: dict[str, Path],
                                            monkeypatch: pytest.MonkeyPatch,
                                            capfd: pytest.CaptureFixture) -> None:
    """``mediatools update --list`` runs the bundled script with the options and its version.

    Args:
        setup: Paths from the fixture.
        monkeypatch: Patch helper (fixture).
        capfd: Output capture on file descriptor level (fixture).
    """
    for key, value in environment(setup).items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("MEDIATOOLS_VERSION")
    assert Cli().main(["update", "--list", "--repo", str(setup["repo"]),
                       "--downloads", str(setup["downloads"])]) == 0
    out = capfd.readouterr().out
    assert "installed release: " in out and "patch archives:    none" in out
    updater = ReleaseUpdater(["-y"])
    assert updater.command()[1:] == [str(SCRIPT), "-y"]
    updater.script = setup["home"] / "missing.sh"
    with pytest.raises(FileNotFoundError):
        updater.command()
