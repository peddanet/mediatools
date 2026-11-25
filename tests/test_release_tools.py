"""Tests of tools/release.sh (GitHub variant) and tools/same-code.sh.

GitHub is replaced by a bare repository (origin) and a fake ``gh`` that keeps
pull requests in a JSON file and merges them with ``git merge --no-ff`` into
the bare repository, like GitHub's merge button.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
TIMEOUT = 300   # seconds per child process: a hanging script fails the test instead of the run
CHANGELOG = """# Changelog

## [Unreleased]

## [1.1.0] - 2026-11-20
"""
FAKE_GH = r'''#!/usr/bin/env python3
"""Stand-in for the GitHub CLI: pull requests, checks, merges, runs."""
import json, os, subprocess, sys, tempfile
state, origin = os.environ["FAKE_GH_DIR"], os.environ["FAKE_GH_ORIGIN"]
args = sys.argv[1:]
with open(os.path.join(state, "calls"), "a", encoding="utf-8") as log:
    log.write(" ".join(args) + "\n")
def opt(name):
    return args[args.index(name) + 1] if name in args else ""
db = os.path.join(state, "prs.json")
prs = json.load(open(db, encoding="utf-8")) if os.path.exists(db) else []
def save():
    json.dump(prs, open(db, "w", encoding="utf-8"))
def fails(what):
    """True while FAKE_GH_FAIL_<WHAT> failures are left (one counted per call)."""
    left = int(os.environ.get("FAKE_GH_FAIL_" + what.upper(), "0"))
    counter = os.path.join(state, "failed-" + what)
    done = int(open(counter, encoding="utf-8").read()) if os.path.exists(counter) else 0
    if done >= left:
        return False
    open(counter, "w", encoding="utf-8").write(str(done + 1))
    return True
cmd = args[:2]
if cmd == ["auth", "status"]:
    sys.exit(0)
if cmd == ["pr", "list"]:
    for number, pr in enumerate(prs, 1):
        if (pr["head"], pr["base"], pr["state"]) == (opt("--head"), opt("--base"), "open"):
            print(number)
            break
    sys.exit(0)
if cmd == ["pr", "create"]:
    if fails("create"):
        if os.environ.get("FAKE_GH_CREATED_ANYWAY"):
            prs.append({"head": opt("--head"), "base": opt("--base"), "state": "open"})
            save()
        sys.exit("GraphQL: Something went wrong while executing your query")
    prs.append({"head": opt("--head"), "base": opt("--base"), "state": "open"})
    save()
    print(f"https://example.invalid/pull/{len(prs)}")
    sys.exit(0)
if cmd == ["pr", "checks"]:
    sys.exit(int(os.environ.get("FAKE_GH_CHECKS", "0")))
if cmd == ["pr", "merge"]:
    if fails("merge"):
        sys.exit("Pull request is not mergeable: the base branch policy prohibits the merge.")
    pr = prs[int(args[2]) - 1]
    with tempfile.TemporaryDirectory() as tmp:
        def run(*command):
            subprocess.run(command, cwd=tmp, check=True, capture_output=True, timeout=300)
        run("git", "clone", "-q", origin, ".")
        run("git", "checkout", "-q", pr["base"])
        run("git", "merge", "-q", "--no-ff", "origin/" + pr["head"], "-m", opt("--subject"))
        run("git", "push", "-q", "origin", pr["base"])
    pr["state"] = "merged"
    save()
    sys.exit(0)
if cmd == ["run", "list"]:
    print(7)
    sys.exit(0)
if cmd == ["run", "watch"]:
    sys.exit(int(os.environ.get("FAKE_GH_RUN", "0")))
sys.exit("fake gh: unsupported " + " ".join(args))
'''


def sh(cwd: Path, env: dict[str, str], *args: str, stdin: str = "",
       check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a command in a directory.

    Args:
        cwd: Working directory.
        env: Environment.
        *args: Command line.
        stdin: Text for standard input.
        check: Raise on a non-zero exit code.

    Returns:
        The finished process.
    """
    return subprocess.run(list(args), cwd=cwd, env=env, input=stdin, text=True,
                          capture_output=True, check=check, timeout=TIMEOUT)


@pytest.fixture(name="repo")
def fixture_repo(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """Create a clone with main, develop, tag v1.1.0 on a bare repository and a fake gh.

    Args:
        tmp_path: Temporary directory (fixture).

    Returns:
        Clone directory and environment.
    """
    home, fake_bin, state = tmp_path / "home", tmp_path / "bin", tmp_path / "gh"
    for folder in (home, fake_bin, state):
        folder.mkdir()
    origin, work = tmp_path / "origin.git", tmp_path / "work"
    (fake_bin / "gh").write_text(FAKE_GH, encoding="utf-8")
    (fake_bin / "gh").chmod(0o755)
    installer = tmp_path / "fake-install.sh"
    installer.write_text('#!/bin/sh\nmkdir -p "$HOME/.local/bin"\necho "$1" > installed\n'
                         'printf \'#!/bin/sh\\necho "demo-tool %s installed"\\n\' "$1"'
                         ' > "$HOME/.local/bin/demo-tool"\n'
                         'chmod +x "$HOME/.local/bin/demo-tool"\n', encoding="utf-8")
    installer.chmod(0o755)
    env = dict(os.environ)
    env.update(HOME=str(home), GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.org",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.org",
               GIT_CONFIG_NOSYSTEM="1", CHECK_CMD="true", RELEASE_WAIT="0", RELEASE_RETRY_PAUSE="0",
               INSTALL_CMD=str(installer), FAKE_GH_DIR=str(state),
               FAKE_GH_ORIGIN=str(origin), PATH=f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    sh(tmp_path, env, "git", "init", "-q", "--bare", "-b", "main", str(origin))
    sh(tmp_path, env, "git", "clone", "-q", str(origin), str(work))
    (work / "src" / "demo_tool").mkdir(parents=True)
    (work / "src" / "demo_tool" / "__init__.py").write_text('__version__ = "1.1.0"\n')
    (work / "CHANGELOG.md").write_text(CHANGELOG)
    (work / "pyproject.toml").write_text('[project]\nname = "demo-tool"\n'
                                         '[tool.setuptools.dynamic]\n'
                                         'version = { attr = "demo_tool.__version__" }\n')
    (work / "tools").mkdir()
    for name in ("release.sh", "same-code.sh"):
        shutil.copy2(TOOLS / name, work / "tools" / name)
    (work / ".gitignore").write_text("installed\n")
    for command in (["git", "switch", "-q", "-c", "main"], ["git", "add", "-A"],
                    ["git", "commit", "-qm", "1.1.0"], ["git", "tag", "-a", "v1.1.0", "-m", "x"],
                    ["git", "branch", "develop"],
                    ["git", "push", "-q", "origin", "main", "develop", "v1.1.0"]):
        sh(work, env, *command)
    return work, env


# pylint: disable-next=too-many-arguments
def commit(work: Path, env: dict[str, str], branch: str, start: str, name: str,
           *, note: str | None = "something") -> None:
    """Create a branch with one new file and, optionally, a changelog line.

    Args:
        work: Clone directory.
        env: Environment.
        branch: New branch.
        start: Branch or commit it starts from.
        name: File to create.
        note: Line under "## [Unreleased]", or None for no changelog change.
    """
    sh(work, env, "git", "switch", "-q", "-c", branch, start)
    (work / name).write_text(name + "\n")
    if note is not None:
        text = (work / "CHANGELOG.md").read_text()
        (work / "CHANGELOG.md").write_text(
            text.replace("## [Unreleased]\n", f"## [Unreleased]\n\n- {note}\n", 1))
    sh(work, env, "git", "add", "-A")
    sh(work, env, "git", "commit", "-qm", name)


def release(work: Path, env: dict[str, str], *args: str, stdin: str = "",
            check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run tools/release.sh.

    Args:
        work: Clone directory.
        env: Environment.
        *args: Arguments of the script.
        stdin: Answers to the questions, one per line.
        check: Raise on a non-zero exit code.

    Returns:
        The finished process.
    """
    return sh(work, env, "tools/release.sh", *args, stdin=stdin, check=check)


def remote(work: Path, env: dict[str, str], ref: str) -> str:
    """Return the commit of a ref on origin ('' if missing).

    Args:
        work: Clone directory.
        env: Environment.
        ref: Full ref name, e.g. ``refs/heads/develop``.

    Returns:
        Commit hash or empty text.
    """
    out = sh(work, env, "git", "ls-remote", "origin", ref).stdout.split()
    return out[0] if out else ""


def subject(work: Path, env: dict[str, str], ref: str) -> str:
    """Return the subject of the newest commit of a ref.

    Args:
        work: Clone directory.
        env: Environment.
        ref: Branch or tag.

    Returns:
        Subject line.
    """
    return sh(work, env, "git", "log", "-1", "--format=%s", ref).stdout.strip()


def test_feature_not_merged_without_yes(repo: tuple[Path, dict[str, str]]) -> None:
    """'n' to both questions pushes nothing and leaves develop unchanged.

    Args:
        repo: Prepared clone (fixture).
    """
    work, env = repo
    before = remote(work, env, "refs/heads/develop")
    commit(work, env, "feature/a", "develop", "a.txt")
    done = release(work, env, stdin="n\nn\n")
    assert "Nicht gepusht" in done.stdout
    assert remote(work, env, "refs/heads/develop") == before
    assert not remote(work, env, "refs/heads/feature/a")


def test_feature_merged_through_pull_request(repo: tuple[Path, dict[str, str]]) -> None:
    """'n' then 'j' merges the rebased feature into develop by a pull request.

    Args:
        repo: Prepared clone (fixture).
    """
    work, env = repo
    commit(work, env, "feature/a", "develop", "a.txt")
    sh(work, env, "git", "switch", "-q", "develop")
    (work / "d.txt").write_text("d\n")
    sh(work, env, "git", "add", "-A")
    sh(work, env, "git", "commit", "-qm", "develop moved on")
    sh(work, env, "git", "push", "-q", "origin", "develop")
    sh(work, env, "git", "switch", "-q", "feature/a")
    done = release(work, env, stdin="n\nj\n")
    assert "in develop gemergt" in done.stdout
    assert subject(work, env, "origin/develop") == "Merge feature/a into develop"
    assert sh(work, env, "git", "rev-parse", "develop").stdout == \
        sh(work, env, "git", "rev-parse", "origin/develop").stdout
    assert not remote(work, env, "refs/heads/feature/a")  # deleted after the merge
    sh(work, env, "git", "cat-file", "-e", "origin/develop:a.txt")
    sh(work, env, "git", "cat-file", "-e", "origin/develop:d.txt")
    assert "v1.2.0" not in sh(work, env, "git", "ls-remote", "--tags", "origin").stdout


def test_merge_only_with_yes(repo: tuple[Path, dict[str, str]]) -> None:
    """--merge-only --yes merges without a release message and releases nothing.

    Args:
        repo: Prepared clone (fixture).
    """
    work, env = repo
    commit(work, env, "feature/a", "develop", "a.txt")
    release(work, env, "--merge-only", "--yes")
    sh(work, env, "git", "cat-file", "-e", "origin/develop:a.txt")
    assert "v1.2.0" not in sh(work, env, "git", "ls-remote", "--tags", "origin").stdout


def test_feature_then_release_and_install(repo: tuple[Path, dict[str, str]]) -> None:
    """A collected and a new feature are released as minor, merged back and installed.

    Args:
        repo: Prepared clone (fixture).
    """
    work, env = repo
    commit(work, env, "feature/a", "develop", "a.txt")
    release(work, env, stdin="n\nj\n")
    commit(work, env, "feature/b", "develop", "b.txt", note="more")
    done = release(work, env, stdin="j\n\nZwei Features\nj\nj\n")
    assert "Release: v1.1.0 -> v1.2.0 aus develop - Zwei Features" in done.stdout
    assert "Bitgleich: JA" in done.stdout
    assert "demo-tool v1.2.0 installed" in done.stdout
    assert (work / "installed").read_text() == "v1.2.0\n"
    assert remote(work, env, "refs/tags/v1.2.0")
    note = sh(work, env, "git", "tag", "-l", "--format=%(contents:subject)", "v1.2.0").stdout
    assert note.strip() == "demo-tool 1.2.0: Zwei Features"
    assert subject(work, env, "origin/main") == "demo-tool 1.2.0: Zwei Features"
    assert subject(work, env, "origin/develop") == "Merge v1.2.0 into develop"
    for branch in ("release/1.2.0", "merge/v1.2.0", "feature/b"):
        assert not remote(work, env, f"refs/heads/{branch}")
    assert '"1.2.0"' in (work / "src/demo_tool/__init__.py").read_text()
    changelog = (work / "CHANGELOG.md").read_text()
    assert "## [Unreleased]\n\n## [1.2.0] - " in changelog and "- more" in changelog
    assert sh(work, env, "tools/same-code.sh", "origin/main", "origin/develop",
              "v1.2.0").returncode == 0


@pytest.mark.parametrize("args,expected", [(("patch",), "v1.1.1"), (("major",), "v2.0.0"),
                                           ((), "v1.2.0")])
def test_develop_non_interactive(repo: tuple[Path, dict[str, str]], args: tuple[str, ...],
                                 expected: str) -> None:
    """--yes with -m releases from develop without questions; minor is the default.

    Args:
        repo: Prepared clone (fixture).
        args: Version part, if any (parameter).
        expected: Expected tag (parameter).
    """
    work, env = repo
    commit(work, env, "feature/a", "develop", "a.txt")
    release(work, env, "--merge-only", "--yes")
    done = release(work, env, *args, "-m", "Neu", "--yes")
    assert f"-> {expected} aus develop" in done.stdout
    assert remote(work, env, f"refs/tags/{expected}")


def test_feature_with_part_from_update(repo: tuple[Path, dict[str, str]]) -> None:
    """The call of 'mediatools update': part, message and --yes on a feature branch.

    Args:
        repo: Prepared clone (fixture).
    """
    work, env = repo
    commit(work, env, "feature/a", "develop", "a.txt")
    done = release(work, env, "minor", "-m", "Sofort", "--yes")
    assert "-> v1.2.0 aus develop" in done.stdout and "Bitgleich: JA" in done.stdout
    assert remote(work, env, "refs/tags/v1.2.0")


def test_release_declined_keeps_everything_local(repo: tuple[Path, dict[str, str]]) -> None:
    """'n' to the release question pushes nothing; the release branch stays local.

    Args:
        repo: Prepared clone (fixture).
    """
    work, env = repo
    commit(work, env, "feature/a", "develop", "a.txt")
    release(work, env, "--merge-only", "--yes")
    sh(work, env, "git", "switch", "-q", "develop")
    done = release(work, env, "minor", "-m", "Spaeter", stdin="n\n")
    assert "Nicht gepusht" in done.stdout and "git branch -D release/1.2.0" in done.stdout
    assert not remote(work, env, "refs/heads/release/1.2.0")
    assert not remote(work, env, "refs/tags/v1.2.0")


def test_hotfix_while_develop_collects(repo: tuple[Path, dict[str, str]]) -> None:
    """A hotfix from main goes into main and develop; develop keeps its features.

    Args:
        repo: Prepared clone (fixture).
    """
    work, env = repo
    commit(work, env, "feature/a", "develop", "a.txt", note=None)
    release(work, env, "--merge-only", "--yes")
    commit(work, env, "hotfix/x", "main", "fix.txt", note="fixed")
    done = release(work, env, "-m", "Dringend", stdin="j\nn\n")
    assert "-> v1.1.1 aus hotfix/x" in done.stdout
    assert "develop enthaelt zusaetzlich" in done.stdout
    assert remote(work, env, "refs/tags/v1.1.1")
    for name in ("a.txt", "fix.txt"):
        sh(work, env, "git", "cat-file", "-e", f"origin/develop:{name}")
    assert sh(work, env, "git", "cat-file", "-e", "v1.1.1:a.txt", check=False).returncode != 0


def test_hotfix_conflict_with_develop(repo: tuple[Path, dict[str, str]]) -> None:
    """A conflict when merging back stops after the release with instructions.

    Args:
        repo: Prepared clone (fixture).
    """
    work, env = repo
    commit(work, env, "feature/a", "develop", "a.txt", note="feature")
    release(work, env, "--merge-only", "--yes")
    commit(work, env, "hotfix/x", "main", "fix.txt", note="fixed")
    stopped = release(work, env, "-m", "Dringend", "--yes", check=False)
    assert "merging it back into develop has conflicts" in stopped.stderr
    assert remote(work, env, "refs/tags/v1.1.1")
    assert not sh(work, env, "git", "status", "--porcelain").stdout


def test_red_checks_merge_nothing(repo: tuple[Path, dict[str, str]]) -> None:
    """Failing required checks stop before the merge.

    Args:
        repo: Prepared clone (fixture).
    """
    work, env = repo
    before = remote(work, env, "refs/heads/develop")
    commit(work, env, "feature/a", "develop", "a.txt")
    stopped = release(work, env | {"FAKE_GH_CHECKS": "1"}, "--merge-only", "--yes", check=False)
    assert "failed - nothing merged" in stopped.stderr
    assert remote(work, env, "refs/heads/develop") == before


@pytest.mark.parametrize("anyway", ["", "1"])
def test_failed_pull_request_creation_is_retried(repo: tuple[Path, dict[str, str]],
                                                 anyway: str) -> None:
    """A passing server error of gh pr create is retried; a PR made anyway is reused.

    Args:
        repo: Prepared clone (fixture).
        anyway: "1" when GitHub created the pull request despite the error.
    """
    work, env = repo
    commit(work, env, "feature/a", "develop", "a.txt")
    done = release(work, env | {"FAKE_GH_FAIL_CREATE": "2", "FAKE_GH_CREATED_ANYWAY": anyway},
                   "--merge-only", "--yes")
    assert "creating the pull request failed (attempt 1/5)" in done.stderr
    assert "Pull Request #1:" in done.stdout
    sh(work, env, "git", "cat-file", "-e", "origin/develop:a.txt")


def test_pull_request_never_created_merges_nothing(repo: tuple[Path, dict[str, str]]) -> None:
    """Five failed attempts stop the script before checks and merge.

    Args:
        repo: Prepared clone (fixture).
    """
    work, env = repo
    before = remote(work, env, "refs/heads/develop")
    commit(work, env, "feature/a", "develop", "a.txt")
    stopped = release(work, env | {"FAKE_GH_FAIL_CREATE": "9"}, "--merge-only", "--yes",
                      check=False)
    assert stopped.returncode != 0
    assert "no pull request for feature/a -> develop - nothing merged" in stopped.stderr
    assert "Pull Request #:" not in stopped.stdout
    calls = (Path(env["FAKE_GH_DIR"]) / "calls").read_text()
    assert "pr checks" not in calls and "pr merge" not in calls
    assert remote(work, env, "refs/heads/develop") == before


def test_refused_merge_is_retried(repo: tuple[Path, dict[str, str]]) -> None:
    """A merge GitHub refuses right after green checks succeeds on a later attempt.

    Args:
        repo: Prepared clone (fixture).
    """
    work, env = repo
    commit(work, env, "feature/a", "develop", "a.txt")
    done = release(work, env | {"FAKE_GH_FAIL_MERGE": "2"}, "--merge-only", "--yes")
    assert "merging pull request #1 failed (attempt 2/5)" in done.stderr
    sh(work, env, "git", "cat-file", "-e", "origin/develop:a.txt")


def test_merge_refused_for_good_stops(repo: tuple[Path, dict[str, str]]) -> None:
    """Five refused merges stop the script with a clear message.

    Args:
        repo: Prepared clone (fixture).
    """
    work, env = repo
    before = remote(work, env, "refs/heads/develop")
    commit(work, env, "feature/a", "develop", "a.txt")
    stopped = release(work, env | {"FAKE_GH_FAIL_MERGE": "9"}, "--merge-only", "--yes",
                      check=False)
    assert "pull request #1 could not be merged" in stopped.stderr
    assert remote(work, env, "refs/heads/develop") == before


def test_failed_tag_check_is_reported(repo: tuple[Path, dict[str, str]]) -> None:
    """A red release check of the tag stops before the merge back.

    Args:
        repo: Prepared clone (fixture).
    """
    work, env = repo
    commit(work, env, "feature/a", "develop", "a.txt")
    stopped = release(work, env | {"FAKE_GH_RUN": "1"}, "minor", "-m", "x", "--yes",
                      check=False)
    assert "release check of v1.2.0 failed" in stopped.stderr


def test_hotfix_refusals(repo: tuple[Path, dict[str, str]]) -> None:
    """A hotfix is always a patch and must not carry unreleased develop commits.

    Args:
        repo: Prepared clone (fixture).
    """
    work, env = repo
    commit(work, env, "feature/a", "develop", "a.txt")
    release(work, env, "--merge-only", "--yes")
    commit(work, env, "hotfix/x", "develop", "fix.txt", note=None)
    assert "always a patch" in release(work, env, "minor", check=False).stderr
    wrong = release(work, env, "-m", "x", check=False)
    assert "must start from main" in wrong.stderr


def test_release_refusals(repo: tuple[Path, dict[str, str]]) -> None:
    """Wrong usage, main, other branches, dirty tree, empty changelog stop the script.

    Args:
        repo: Prepared clone (fixture).
    """
    work, env = repo
    assert "usage" in release(work, env, "huge", check=False).stderr
    assert "Usage" in release(work, env, "--help").stdout
    assert "needs the release message" in release(work, env, "--yes", check=False).stderr
    assert "exclude each other" in release(work, env, "minor", "--merge-only",
                                           check=False).stderr
    assert "main only receives" in release(work, env, check=False).stderr
    sh(work, env, "git", "switch", "-q", "-c", "topic")
    assert "expected feature" in release(work, env, check=False).stderr
    sh(work, env, "git", "switch", "-q", "develop")
    assert "for feature branches" in release(work, env, "--merge-only", check=False).stderr
    assert "nothing to release" in release(work, env, check=False).stderr
    (work / "dirty.txt").write_text("x")
    assert "uncommitted" in release(work, env, check=False).stderr
    (work / "dirty.txt").unlink()
    commit(work, env, "feature/a", "develop", "a.txt", note=None)
    empty = release(work, env, "minor", "-m", "x", "--yes", check=False)
    assert "nothing under" in empty.stderr


def test_empty_message_refused(repo: tuple[Path, dict[str, str]]) -> None:
    """Without a release message nothing is released.

    Args:
        repo: Prepared clone (fixture).
    """
    work, env = repo
    commit(work, env, "feature/a", "develop", "a.txt")
    release(work, env, "--merge-only", "--yes")
    sh(work, env, "git", "switch", "-q", "develop")
    stopped = release(work, env, stdin="\n\n", check=False)
    assert "release message is empty" in stopped.stderr
    assert not remote(work, env, "refs/tags/v1.2.0")


def test_gates_run_before_anything_is_pushed(repo: tuple[Path, dict[str, str]]) -> None:
    """Failing gates push nothing on a feature and release nothing on develop.

    Args:
        repo: Prepared clone (fixture).
    """
    work, env = repo
    failing = env | {"CHECK_CMD": "false"}
    commit(work, env, "feature/a", "develop", "a.txt")
    stopped = release(work, failing, "--merge-only", "--yes", check=False)
    assert "develop is unchanged" in stopped.stderr
    assert not remote(work, env, "refs/heads/feature/a")
    release(work, env, "--merge-only", "--yes")
    sh(work, env, "git", "switch", "-q", "develop")
    stopped = release(work, failing, "-m", "x", "--yes", check=False)
    assert "nothing was released" in stopped.stderr
    assert '"1.1.0"' in (work / "src/demo_tool/__init__.py").read_text()
    assert not sh(work, env, "git", "status", "--porcelain").stdout
    assert sh(work, env, "git", "branch", "--list", "release/*").stdout == ""


def test_rebase_conflict_changes_nothing(repo: tuple[Path, dict[str, str]]) -> None:
    """A feature that conflicts with develop is left as it was.

    Args:
        repo: Prepared clone (fixture).
    """
    work, env = repo
    commit(work, env, "feature/a", "develop", "same.txt")
    commit(work, env, "feature/b", "develop", "same.txt", note="other")
    (work / "same.txt").write_text("different\n")
    sh(work, env, "git", "commit", "-qam", "conflict")
    release(work, env, "--merge-only", "--yes")
    sh(work, env, "git", "switch", "-q", "feature/a")
    head = sh(work, env, "git", "rev-parse", "HEAD").stdout
    stopped = release(work, env, check=False)
    assert "has conflicts - nothing changed" in stopped.stderr
    assert sh(work, env, "git", "rev-parse", "HEAD").stdout == head
    assert not sh(work, env, "git", "status", "--porcelain").stdout


def test_same_code_yes_no(repo: tuple[Path, dict[str, str]]) -> None:
    """JA for identical trees, NEIN with a file list otherwise, 2 for bad usage.

    Args:
        repo: Prepared clone (fixture).
    """
    work, env = repo
    assert "Bitgleich: JA" in sh(work, env, "tools/same-code.sh", "main", "develop").stdout
    commit(work, env, "hotfix/x", "develop", "fix.txt")
    other = sh(work, env, "tools/same-code.sh", "main", "hotfix/x", check=False)
    assert other.returncode == 1 and "NEIN" in other.stdout and "fix.txt" in other.stdout
    assert sh(work, env, "tools/same-code.sh", "main", check=False).returncode == 2
    assert sh(work, env, "tools/same-code.sh", "main", "nope", check=False).returncode == 2
