"""Tests of tools/github/release.sh (GitHub variant) and tools/same-code.sh (bundle).

GitHub is replaced by a bare repository (origin) and a fake ``gh`` that keeps
pull requests in a JSON file and merges them with ``git merge --no-ff`` into
the bare repository, like GitHub's merge button. With FAKE_PROTECT=1 a
pre-receive hook of origin refuses direct pushes to main and develop, like a
branch protection that wants pull requests; only the merge of the fake gh
passes.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
# in the bundle tools/github/release.sh, in a GitHub project tools/release.sh: the file is copied unchanged
GITHUB_RELEASE = TOOLS / "github" / "release.sh" if (TOOLS / "github").is_dir() else TOOLS / "release.sh"
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
if cmd == ["pr", "checks"] and "--json" in args:
    # like gh 2.45 (Ubuntu 24.04): no --json for this command
    sys.exit("unknown flag: --json\n\nUsage:  gh pr checks [<number> | <url> | <branch>] [flags]")
if cmd == ["pr", "checks"] and "--required" not in args:
    # without --required GitHub also lists the finished checks of other runs on
    # the same commit (the tag run when merge/vX.Y.Z is the tagged commit)
    print("release-check\tpass\t5s\thttps://github.com/o/r/actions/runs/40/job/7\t")
    sys.exit(0)
if cmd == ["pr", "checks"] and os.environ.get("FAKE_GH_MOVE") and not os.path.exists(
        os.path.join(state, "moved")):
    # parallel work: somebody else pushes to develop while the checks run
    open(os.path.join(state, "moved"), "w", encoding="utf-8").write("1")
    with tempfile.TemporaryDirectory() as tmp:
        quiet = {"cwd": tmp, "check": True, "capture_output": True, "timeout": 300}
        subprocess.run(["git", "clone", "-q", "-b", "develop", origin, "."], **quiet)
        open(os.path.join(tmp, "other.txt"), "w", encoding="utf-8").write("other\n")
        subprocess.run(["git", "add", "-A"], **quiet)
        subprocess.run(["git", "commit", "-qm", "other work"], **quiet)
        subprocess.run(["git", "push", "-q", "origin", "develop"], **quiet,
                       env=dict(os.environ, FAKE_GH_MERGING="1"))
if cmd == ["pr", "checks"]:
    # registration: the first FAKE_GH_LATE answers (all with FAKE_GH_LISTED=0)
    # have no checks yet; check-polls counts the answers until the first list
    marker = os.path.join(state, "registered")
    if not os.path.exists(marker):
        polls = os.path.join(state, "check-polls")
        count = (int(open(polls, encoding="utf-8").read()) if os.path.exists(polls) else 0) + 1
        open(polls, "w", encoding="utf-8").write(str(count))
        if os.environ.get("FAKE_GH_LISTED") == "0" or count <= int(os.environ.get("FAKE_GH_LATE", "0")):
            sys.exit("no checks reported on the 'feature/a' branch")
        open(marker, "w", encoding="utf-8").write("1")
    # FAKE_GH_STATES: state per query, comma separated, the last one repeats;
    # default from FAKE_GH_CHECKS (1 = fail, else pass)
    states = (os.environ.get("FAKE_GH_STATES") or (
        "fail" if os.environ.get("FAKE_GH_CHECKS") == "1" else "pass")).split(",")
    queries = os.path.join(state, "state-queries")
    count = int(open(queries, encoding="utf-8").read()) if os.path.exists(queries) else 0
    open(queries, "w", encoding="utf-8").write(str(count + 1))
    bucket = states[min(count, len(states) - 1)]
    run = "https://github.com/o/r/actions/runs/41/job/"
    print(f"gates (3.12)\tpass\t2m1s\t{run}8\t\ngates (3.14)\t{bucket}\t2m3s\t{run}9\t")
    sys.exit({"fail": 1, "pending": 8}.get(bucket, 0))
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
        subprocess.run(["git", "push", "-q", "origin", pr["base"]], cwd=tmp, check=True,
                       capture_output=True, timeout=300, env=dict(os.environ, FAKE_GH_MERGING="1"))
    pr["state"] = "merged"
    save()
    sys.exit(0)
if cmd == ["run", "list"]:
    print(os.environ.get("FAKE_GH_RUN_ID", "7"))
    sys.exit(0)
if cmd == ["run", "view"] and opt("--json") == "jobs":
    # first answer: job queued; then the step "gate: tests" runs
    views = os.path.join(state, "job-views")
    count = int(open(views, encoding="utf-8").read()) if os.path.exists(views) else 0
    open(views, "w", encoding="utf-8").write(str(count + 1))
    names = ["Set up job", "Run actions/setup-python@v6", "gate: privacy", "gate: tests"]
    if count == 0:
        job = {"name": "gates (3.14)", "status": "queued", "startedAt": None, "steps": []}
    else:
        steps = [{"name": n, "status": "completed", "conclusion": "success"} for n in names[:3]]
        steps.append({"name": names[3], "status": "in_progress", "conclusion": ""})
        job = {"name": "gates (3.14)", "status": "in_progress",
               "startedAt": "2026-11-26T10:00:00Z", "steps": steps}
    print(json.dumps({"jobs": [{"name": "gates (3.12)", "status": "completed", "steps": []}, job]}))
    sys.exit(0)
if cmd == ["run", "view"] and "--json" in args:
    # first query: in progress; then the result of FAKE_GH_RUN (0 = success)
    views = os.path.join(state, "run-views")
    count = int(open(views, encoding="utf-8").read()) if os.path.exists(views) else 0
    open(views, "w", encoding="utf-8").write(str(count + 1))
    if count == 0:
        print("in_progress ")
    else:
        print("completed " + ("success" if os.environ.get("FAKE_GH_RUN", "0") == "0" else "failure"))
    sys.exit(0)
if cmd == ["run", "view"]:
    print("FAILED tests/test_x.py::test_y - AssertionError")
    sys.exit(0)
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
               GIT_CONFIG_NOSYSTEM="1", NO_COLOR="1", CHECK_CMD="true",
               RELEASE_WAIT="0", RELEASE_POLL="0", RELEASE_RETRY_PAUSE="0",
               INSTALL_CMD=str(installer), FAKE_GH_DIR=str(state),
               FAKE_GH_ORIGIN=str(origin), PATH=f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    sh(tmp_path, env, "git", "init", "-q", "--bare", "-b", "main", str(origin))
    hook = origin / "hooks" / "pre-receive"
    hook.write_text('#!/bin/sh\n[ "$FAKE_PROTECT" = 1 ] && [ -z "$FAKE_GH_MERGING" ] || exit 0\n'
                    'while read -r old new ref; do\n  case "$ref" in refs/heads/main|refs/heads/develop)\n'
                    '    [ "$old" = 0000000000000000000000000000000000000000 ] && continue\n'
                    '    echo "GH006: Protected branch update failed for $ref." >&2; exit 1 ;;\n'
                    '  esac\ndone\n')
    hook.chmod(0o755)
    sh(tmp_path, env, "git", "clone", "-q", str(origin), str(work))
    (work / "src" / "demo_tool").mkdir(parents=True)
    (work / "src" / "demo_tool" / "__init__.py").write_text('__version__ = "1.1.0"\n')
    (work / "CHANGELOG.md").write_text(CHANGELOG)
    (work / "pyproject.toml").write_text('[project]\nname = "demo-tool"\n'
                                         '[tool.setuptools.dynamic]\n'
                                         'version = { attr = "demo_tool.__version__" }\n')
    (work / "tools").mkdir()
    shutil.copy2(GITHUB_RELEASE, work / "tools" / "release.sh")
    shutil.copy2(TOOLS / "output.sh", work / "tools" / "output.sh")
    shutil.copy2(TOOLS / "same-code.sh", work / "tools" / "same-code.sh")
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
    assert "Not pushed" in done.stdout
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
    assert "is merged into develop" in done.stdout
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
    done = release(work, env, stdin="j\n\nTwo features\nj\nj\n")
    assert "Release: v1.1.0 -> v1.2.0 from develop - Two features" in done.stdout
    assert "Bit-identical: YES" in done.stdout
    assert "demo-tool v1.2.0 installed" in done.stdout
    assert (work / "installed").read_text() == "v1.2.0\n"
    assert remote(work, env, "refs/tags/v1.2.0")
    note = sh(work, env, "git", "tag", "-l", "--format=%(contents:subject)", "v1.2.0").stdout
    assert note.strip() == "demo-tool 1.2.0: Two features"
    assert subject(work, env, "origin/main") == "demo-tool 1.2.0: Two features"
    # one CI run for the release: main, develop and the tag are the checked commit
    assert remote(work, env, "refs/heads/develop") == remote(work, env, "refs/heads/main")
    assert remote(work, env, "refs/tags/v1.2.0^{}") == remote(work, env, "refs/heads/main")
    assert sh(work, env, "git", "log", "-1", "--format=%an", "origin/main").stdout.strip() == "t"
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
    done = release(work, env, *args, "-m", "New", "--yes")
    assert f"-> {expected} from develop" in done.stdout
    assert remote(work, env, f"refs/tags/{expected}")


def test_feature_with_part_from_update(repo: tuple[Path, dict[str, str]]) -> None:
    """The call of 'mediatools update': part, message and --yes on a feature branch.

    Args:
        repo: Prepared clone (fixture).
    """
    work, env = repo
    commit(work, env, "feature/a", "develop", "a.txt")
    done = release(work, env, "minor", "-m", "Sofort", "--yes")
    assert "-> v1.2.0 from develop" in done.stdout and "Bit-identical: YES" in done.stdout
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
    done = release(work, env, "minor", "-m", "Later", stdin="n\n")
    assert "Not pushed" in done.stdout and "git branch -D release/1.2.0" in done.stdout
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
    done = release(work, env, "-m", "Urgent", stdin="j\nn\n")
    assert "-> v1.1.1 from hotfix/x" in done.stdout
    assert "develop also holds the features" in done.stdout
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
    stopped = release(work, env, "-m", "Urgent", "--yes", check=False)
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
    """YES for identical trees, NO with a file list otherwise, 2 for bad usage.

    Args:
        repo: Prepared clone (fixture).
    """
    work, env = repo
    assert "Bit-identical: YES" in sh(work, env, "tools/same-code.sh", "main", "develop").stdout
    commit(work, env, "hotfix/x", "develop", "fix.txt")
    other = sh(work, env, "tools/same-code.sh", "main", "hotfix/x", check=False)
    assert other.returncode == 1 and "NO -" in other.stdout and "fix.txt" in other.stdout
    assert sh(work, env, "tools/same-code.sh", "main", check=False).returncode == 2
    assert sh(work, env, "tools/same-code.sh", "main", "nope", check=False).returncode == 2


def test_waits_until_checks_are_registered(repo: tuple[Path, dict[str, str]]) -> None:
    """The script asks GitHub again until the checks are registered, then merges.

    Args:
        repo: Prepared clone (fixture).
    """
    work, env = repo
    commit(work, env, "feature/a", "develop", "a.txt")
    late = env | {"FAKE_GH_LATE": "2", "RELEASE_WAIT": "30"}
    done = release(work, late, "--merge-only", "--yes")
    assert "is merged into develop" in done.stdout
    polls = Path(env["FAKE_GH_DIR"]) / "check-polls"
    assert polls.read_text(encoding="utf-8") == "3"
    sh(work, env, "git", "cat-file", "-e", "origin/develop:a.txt")


def test_no_checks_registered_merges_nothing(repo: tuple[Path, dict[str, str]]) -> None:
    """Without registered checks the script stops after RELEASE_WAIT and merges nothing.

    Args:
        repo: Prepared clone (fixture).
    """
    work, env = repo
    before = remote(work, env, "refs/heads/develop")
    commit(work, env, "feature/a", "develop", "a.txt")
    stopped = release(work, env | {"FAKE_GH_LISTED": "0"}, "--merge-only", "--yes",
                      check=False)
    assert stopped.returncode == 1
    assert "registered no checks for pull request #1 within 0 s" in stopped.stderr
    assert "last answer of gh: no checks reported on the 'feature/a' branch" in stopped.stderr
    assert remote(work, env, "refs/heads/develop") == before


def test_no_tag_run_is_reported(repo: tuple[Path, dict[str, str]]) -> None:
    """A tag whose CI run never appears stops before the merge back.

    Args:
        repo: Prepared clone (fixture).
    """
    work, env = repo
    commit(work, env, "feature/a", "develop", "a.txt")
    stopped = release(work, env | {"FAKE_GH_RUN_ID": ""}, "minor", "-m", "x", "--yes",
                      check=False)
    assert "no CI run appeared for v1.2.0 within 0 s" in stopped.stderr
    assert remote(work, env, "refs/tags/v1.2.0")


def test_time_measurement(repo: tuple[Path, dict[str, str]]) -> None:
    """Every long step prints its duration; the summary follows a release and a stop.

    Args:
        repo: Prepared clone (fixture).
    """
    work, env = repo
    commit(work, env, "feature/a", "develop", "a.txt")
    done = release(work, env, "minor", "-m", "New", "--yes")
    for step in ("Gates release/1.2.0", "Push release/1.2.0", "Registration of the checks of PR #1",
                 "Checks of PR #1", "Push main, develop and v1.2.0",
                 "Registration of the release check of v1.2.0",
                 "Release check of v1.2.0", "Installation v1.2.0"):
        assert f"] {step}: " in done.stdout, step
    assert "] Gates feature/a: " not in done.stdout  # one gate run on the released state
    summary = done.stdout.split("Time measurement release.sh ", 1)[1].split("══ Result", 1)[0]
    assert "Checks of PR #1" in summary and "Total" in summary.strip().splitlines()[-1]
    commit(work, env, "feature/b", "develop", "b.txt")
    stopped = release(work, env | {"FAKE_GH_CHECKS": "1"}, "--merge-only", "--yes",
                      check=False)
    assert stopped.returncode == 1
    assert "Time measurement release.sh" in stopped.stdout and "Checks of PR #2" in stopped.stdout
    answered = release(work, env, stdin="n\nn\n")
    assert "] Answer to: Build the release now?: " in answered.stdout
    assert "══ Time measurement" not in release(work, env, "--help").stdout


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
    done = release(work, env | {"FAKE_GH_FAIL_MERGE": "2", "FAKE_PROTECT": "1"}, "--merge-only", "--yes")
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
    stopped = release(work, env | {"FAKE_GH_FAIL_MERGE": "9", "FAKE_PROTECT": "1"}, "--merge-only", "--yes",
                      check=False)
    assert "pull request #1 could not be merged" in stopped.stderr
    assert remote(work, env, "refs/heads/develop") == before


def test_check_watcher_prints_changes(repo: tuple[Path, dict[str, str]]) -> None:
    """One CI line per change of a check, none while nothing changes.

    Args:
        repo: Prepared clone (fixture).
    """
    work, env = repo
    commit(work, env, "feature/a", "develop", "a.txt")
    done = release(work, env | {"FAKE_GH_STATES": "pending,pending,pending,pass"},
                   "--merge-only", "--yes")
    lines = [line for line in done.stdout.splitlines()
             if "gates (3.14)" in line and ("… pending" in line or "✔ pass" in line)]
    assert len(lines) == 2 and "pending" in lines[0] and "pass" in lines[1]
    assert sum("gates (3.12)" in line for line in done.stdout.splitlines()) == 1


def test_red_check_shows_failed_log(repo: tuple[Path, dict[str, str]]) -> None:
    """A red check prints the failed log of its run and merges nothing.

    Args:
        repo: Prepared clone (fixture).
    """
    work, env = repo
    commit(work, env, "feature/a", "develop", "a.txt")
    stopped = release(work, env | {"FAKE_GH_STATES": "pending,fail"}, "--merge-only", "--yes",
                      check=False)
    assert stopped.returncode == 1
    assert "failed log of run 41" in stopped.stdout and "AssertionError" in stopped.stdout
    assert "nothing merged" in stopped.stderr


def test_output_library_required(repo: tuple[Path, dict[str, str]]) -> None:
    """Without tools/output.sh the script stops before it does anything.

    Args:
        repo: Prepared clone (fixture).
    """
    work, env = repo
    (work / "tools" / "output.sh").unlink()
    stopped = release(work, env, "--help", check=False)
    assert stopped.returncode == 2 and "tools/output.sh is missing" in stopped.stderr


def test_result_block(repo: tuple[Path, dict[str, str]]) -> None:
    """A run ends with the result block: DONE after a release, FAILED after a stop.

    Args:
        repo: Prepared clone (fixture).
    """
    work, env = repo
    commit(work, env, "feature/a", "develop", "a.txt")
    done = release(work, env, "minor", "-m", "New", "--yes")
    assert "══ Result" in done.stdout and "DONE  v1.2.0 is published" in done.stdout
    assert "run 7" in done.stdout and "success" in done.stdout
    commit(work, env, "feature/b", "develop", "b.txt")
    stopped = release(work, env | {"FAKE_GH_CHECKS": "1"}, "--merge-only", "--yes", check=False)
    assert "FAILED" in stopped.stdout and "✘ release.sh:" in stopped.stderr
    assert "══ Result" not in release(work, env, "--help").stdout


def test_registration_wait_is_visible(repo: tuple[Path, dict[str, str]]) -> None:
    """While GitHub has no checks yet, the answer of gh is shown, never a silent wait.

    Args:
        repo: Prepared clone (fixture).
    """
    work, env = repo
    commit(work, env, "feature/a", "develop", "a.txt")
    done = release(work, env | {"FAKE_GH_LATE": "2", "RELEASE_WAIT": "30"}, "--merge-only", "--yes")
    shown = [line for line in done.stdout.splitlines() if "gh: no checks reported" in line]
    assert len(shown) == 1 and "PR #1" in shown[0]


def test_progress_of_running_jobs(repo: tuple[Path, dict[str, str]]) -> None:
    """A running check shows a progress line per job: bar, done/total, running step.

    Args:
        repo: Prepared clone (fixture).
    """
    work, env = repo
    commit(work, env, "feature/a", "develop", "a.txt")
    done = release(work, env | {"FAKE_GH_STATES": "pending,pending,pending,pass"},
                   "--merge-only", "--yes")
    queued = [line for line in done.stdout.splitlines() if "queued for a runner" in line]
    running = [line for line in done.stdout.splitlines() if "gate: tests" in line]
    assert len(queued) == 1 and "gates (3.14)" in queued[0] and "0/0" in queued[0]
    assert len(running) == 1 and "3/4" in running[0] and "█" in running[0] and " s" in running[0]
    assert not any("gates (3.12)" in line and "/" in line for line in done.stdout.splitlines())


def test_heartbeat_repeats_progress(repo: tuple[Path, dict[str, str]]) -> None:
    """With RELEASE_HEARTBEAT=0 every query prints the progress again (shows a hang).

    Args:
        repo: Prepared clone (fixture).
    """
    work, env = repo
    commit(work, env, "feature/a", "develop", "a.txt")
    done = release(work, env | {"FAKE_GH_STATES": "pending,pending,pending,pending,pass",
                                "RELEASE_HEARTBEAT": "0"}, "--merge-only", "--yes")
    assert sum("gate: tests" in line for line in done.stdout.splitlines()) >= 2


def test_pr_checks_without_json(repo: tuple[Path, dict[str, str]]) -> None:
    """The script never asks "gh pr checks --json" (missing in gh 2.45 of Ubuntu 24.04).

    Args:
        repo: Prepared clone (fixture).
    """
    work, env = repo
    commit(work, env, "feature/a", "develop", "a.txt")
    release(work, env | {"FAKE_GH_STATES": "pending,fail"}, "--merge-only", "--yes", check=False)
    calls = (Path(env["FAKE_GH_DIR"]) / "calls").read_text(encoding="utf-8").splitlines()
    assert any(call.startswith("pr checks") for call in calls)
    assert not any(call.startswith("pr checks") and "--json" in call for call in calls)


def test_only_required_checks_count(repo: tuple[Path, dict[str, str]]) -> None:
    """Every query asks for the required checks only (finished tag runs must not count).

    Args:
        repo: Prepared clone (fixture).
    """
    work, env = repo
    commit(work, env, "feature/a", "develop", "a.txt")
    done = release(work, env | {"FAKE_GH_LATE": "1", "RELEASE_WAIT": "30"}, "--merge-only", "--yes")
    calls = (Path(env["FAKE_GH_DIR"]) / "calls").read_text(encoding="utf-8").splitlines()
    checks = [call for call in calls if call.startswith("pr checks")]
    assert checks and all("--required" in call for call in checks)
    assert "release-check" not in done.stdout


def test_one_check_run_per_release(repo: tuple[Path, dict[str, str]]) -> None:
    """Feature plus release: one pull request, its checks on the commit that lands everywhere.

    Args:
        repo: Prepared clone (fixture).
    """
    work, env = repo
    commit(work, env, "feature/a", "develop", "a.txt")
    done = release(work, env, "minor", "-m", "New", "--yes")
    calls = (Path(env["FAKE_GH_DIR"]) / "calls").read_text(encoding="utf-8")
    assert calls.count("pr create") == 1 and "pr merge" not in calls
    assert "Release v1.2.0 · step 2/3 · GitHub: required checks of" in done.stdout
    main = remote(work, env, "refs/heads/main")
    assert main == remote(work, env, "refs/heads/develop") == remote(work, env, "refs/tags/v1.2.0^{}")
    assert subject(work, env, "origin/main") == "demo-tool 1.2.0: New"
    assert subject(work, env, "origin/main^2^") == "Merge feature/a into develop"  # git-flow history kept
    for branch in ("feature/a", "release/1.2.0"):
        assert not remote(work, env, f"refs/heads/{branch}")


def test_protected_branches_fall_back_to_pull_requests(repo: tuple[Path, dict[str, str]]) -> None:
    """When GitHub refuses the direct push, the pull requests are merged as before.

    Args:
        repo: Prepared clone (fixture).
    """
    work, env = repo
    env = env | {"FAKE_PROTECT": "1"}
    commit(work, env, "feature/a", "develop", "a.txt")
    done = release(work, env, "minor", "-m", "New", "--yes")
    assert "GitHub refused the direct push" in done.stdout and "Bit-identical: YES" in done.stdout
    assert subject(work, env, "origin/main") == "demo-tool 1.2.0: New"
    assert subject(work, env, "origin/develop") == "Merge v1.2.0 into develop"
    assert remote(work, env, "refs/tags/v1.2.0^{}") == remote(work, env, "refs/heads/main")
    commit(work, env, "feature/b", "develop", "b.txt")
    merged = release(work, env, "--merge-only", "--yes")
    assert "GitHub refused the direct push" in merged.stdout
    sh(work, env, "git", "cat-file", "-e", "origin/develop:b.txt")


def test_parallel_work_stops_before_publishing(repo: tuple[Path, dict[str, str]]) -> None:
    """Somebody pushes to develop while the checks run: nothing is published, local refs undone.

    Args:
        repo: Prepared clone (fixture).
    """
    work, env = repo
    main_before = remote(work, env, "refs/heads/main")
    commit(work, env, "feature/a", "develop", "a.txt")
    stopped = release(work, env | {"FAKE_GH_MOVE": "1"}, "minor", "-m", "New", "--yes", check=False)
    assert stopped.returncode == 1
    assert "changed on origin while this run waited (parallel work)" in stopped.stderr
    assert remote(work, env, "refs/heads/main") == main_before
    assert not remote(work, env, "refs/tags/v1.2.0")
    assert sh(work, env, "git", "tag", "--list", "v1.2.0").stdout == ""
    assert sh(work, env, "git", "rev-parse", "main").stdout.strip() == main_before
    assert sh(work, env, "git", "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "feature/a"
    sh(work, env, "git", "cat-file", "-e", "feature/a:a.txt")  # the work is kept


def test_failed_checks_leave_local_branches_as_on_origin(repo: tuple[Path, dict[str, str]]) -> None:
    """Red checks: the local merge into develop is undone, a new run can start at once.

    Args:
        repo: Prepared clone (fixture).
    """
    work, env = repo
    commit(work, env, "feature/a", "develop", "a.txt")
    stopped = release(work, env | {"FAKE_GH_CHECKS": "1"}, "--merge-only", "--yes", check=False)
    assert stopped.returncode == 1 and "back at the state of origin" in stopped.stdout
    assert sh(work, env, "git", "rev-parse", "develop").stdout == sh(
        work, env, "git", "rev-parse", "origin/develop").stdout
    release(work, env, "--merge-only", "--yes")
    sh(work, env, "git", "cat-file", "-e", "origin/develop:a.txt")
