"""1.2.13: every CI run of ``mediatools update`` is announced and summarised.

The functions of ``apply-release.sh`` that announce, follow and tabulate
the CI runs are loaded into a bash process together with a stand-in
``gh`` that answers like GitHub; no network is used.
"""
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "src" / "mediatools" / "update" / "apply-release.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"

FAKE_GH = """#!/bin/sh
case "$*" in
  *"--jq length"*) echo 1 ;;
  "run list"*) echo 4711 ;;
  *"--json url"*) echo https://example.invalid/runs/4711 ;;
  *"status,conclusion"*) echo "completed $FAKE_CONCLUSION" ;;
  "run watch"*|"pr checks"*) exit "$FAKE_RC" ;;
esac
"""


def functions() -> str:
    """Return the function block of the script (from ``step`` up to the inventory).

    Returns:
        Bash source of the helper functions.
    """
    lines = SCRIPT.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("step() {"))
    end = next(i for i, line in enumerate(lines) if line.startswith("# -- inventory"))
    return "\n".join(lines[start:end])


def bash(tmp_path: Path, body: str, conclusion: str = "success", rc: int = 0) -> tuple[int, str]:
    """Run the helper functions plus ``body`` with the stand-in ``gh``.

    Args:
        tmp_path: Temporary directory.
        body: Bash commands using the functions.
        conclusion: Conclusion the stand-in reports for the run.
        rc: Exit code of ``gh run watch`` / ``gh pr checks``.

    Returns:
        Exit code and combined output.
    """
    fake = tmp_path / "bin" / "gh"
    fake.parent.mkdir(exist_ok=True)
    fake.write_text(FAKE_GH, encoding="utf-8")
    fake.chmod(0o755)
    env = dict(os.environ, PATH=f"{fake.parent}{os.pathsep}{os.environ['PATH']}",
               FAKE_CONCLUSION=conclusion, FAKE_RC=str(rc), GH_PAUSE="0")
    result = subprocess.run(["bash", "-c", functions() + "\n" + body], env=env,
                            capture_output=True, text=True, check=False, timeout=60)
    return result.returncode, result.stdout + result.stderr


def test_run_is_announced_with_purpose_and_url(tmp_path: Path) -> None:
    """A CI run prints trigger, commit, what it checks, why, and its URL.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    code, out = bash(tmp_path, 'ci_run "1.2.13 CI 1/3" hotfix/1.2.13 pull_request '
                               'abcdef1234 "merge result" "required check" PRURL; ci_summary')
    assert code == 0
    assert "--- 1.2.13 CI 1/3: pull_request on hotfix/1.2.13 (commit abcdef1)" in out
    assert "checks:  merge result" in out and "purpose: required check" in out
    assert "run:     https://example.invalid/runs/4711" in out
    table = out.split("CI runs on GitHub:")[1].splitlines()
    assert table[1].split() == ["run", "trigger", "result", "url"]
    assert table[2].split() == ["1.2.13", "CI", "1/3", "pull_request", "hotfix/1.2.13",
                                "success", "https://example.invalid/runs/4711"]


def test_failed_run_is_recorded_and_returned(tmp_path: Path) -> None:
    """A red run returns the watcher's exit code and appears as failure in the table.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    code, out = bash(tmp_path, 'ci_run "1.2.13 CI 2/3" v1.2.13 push abcdef1234 a b '
                               '|| { rc=$?; ci_summary; exit "$rc"; }',
                     conclusion="failure", rc=1)
    assert code == 1
    assert "failure" in out.split("CI runs on GitHub:")[1]


@pytest.mark.parametrize("body", ["ci_summary", 'local_gates hotfix/1.2.13'])
def test_summary_empty_and_local_announcement(tmp_path: Path, body: str) -> None:
    """No table without CI runs; the local gate run names the branch.

    Args:
        tmp_path: Temporary directory (fixture).
        body: Command under test.
    """
    code, out = bash(tmp_path, body)
    assert code == 0
    if body == "ci_summary":
        assert out == ""
    else:
        assert "local gates (pre-push hook)" in out and "hotfix/1.2.13 has no push trigger" in out


def test_workflow_triggers_and_matrix() -> None:
    """Only tags and pull requests start runs; the matrix holds the oldest and newest Python."""
    flow = WORKFLOW.read_text(encoding="utf-8")
    trigger = flow.split("\non:\n", 1)[1].split("\njobs:", 1)[0]
    assert trigger.split() == ["push:", "tags:", '["v*"]', "pull_request:", "branches:",
                               "[main,", "develop]", "workflow_dispatch:"]
    assert 'python: ["3.10", "3.14"]' in flow
    assert "actions/setup-python@v6" in flow and "setup-python@v5" not in flow
