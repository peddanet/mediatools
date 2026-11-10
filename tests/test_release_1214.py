"""1.2.14: a short loss of the GitHub connection does not stop ``mediatools update``.

Reading ``gh`` calls are retried, and an interrupted watch of a CI run is
resumed until GitHub reports the run as completed. A stand-in ``gh``
answers from a scripted list, one line per call.
"""
import os
import subprocess
from pathlib import Path

from test_release_1213 import functions

ROOT = Path(__file__).resolve().parent.parent

FAKE_GH = """#!/bin/sh
# each call takes the next line of $FAKE_DIR/script: "<exit code> <output>"
n=$(cat "$FAKE_DIR/count" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$FAKE_DIR/count"
echo "$*" >> "$FAKE_DIR/calls"
line=$(sed -n "${n}p" "$FAKE_DIR/script")
code=${line%% *}; out=${line#* }
[ "$out" = "-" ] || echo "$out"
exit "$code"
"""


def bash(tmp_path: Path, body: str, answers: list[str]) -> tuple[int, str, list[str]]:
    """Run the script functions plus ``body`` against scripted ``gh`` answers.

    Args:
        tmp_path: Temporary directory.
        body: Bash commands using the functions.
        answers: One ``"<exit code> <output>"`` per gh call (``-`` = no output).

    Returns:
        Exit code, combined output and the recorded gh calls.
    """
    fake = tmp_path / "bin" / "gh"
    fake.parent.mkdir()
    fake.write_text(FAKE_GH, encoding="utf-8")
    fake.chmod(0o755)
    (tmp_path / "script").write_text("\n".join(answers) + "\n", encoding="utf-8")
    env = dict(os.environ, PATH=f"{fake.parent}{os.pathsep}{os.environ['PATH']}",
               FAKE_DIR=str(tmp_path), GH_PAUSE="0")
    result = subprocess.run(["bash", "-c", functions() + "\n" + body], env=env,
                            capture_output=True, text=True, check=False, timeout=60)
    calls = (tmp_path / "calls").read_text(encoding="utf-8").splitlines()
    return result.returncode, result.stdout + result.stderr, calls


def test_read_is_retried_until_it_succeeds(tmp_path: Path) -> None:
    """Two failed attempts are reported, the third answer is used.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    code, out, calls = bash(tmp_path, 'x=$(gh_read run list --limit 1); echo "got $x"',
                            ["1 -", "1 -", "0 4711"])
    assert code == 0 and "got 4711" in out
    assert out.count("failed (attempt") == 2 and len(calls) == 3


def test_read_gives_up_after_five_attempts(tmp_path: Path) -> None:
    """After five failures the call fails and says so.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    code, out, calls = bash(tmp_path, "gh_read run list", ["1 -"] * 6)
    assert code == 1 and "failed 5 times, giving up" in out and len(calls) == 5


def test_interrupted_watch_is_resumed(tmp_path: Path) -> None:
    """A watch that ends while the run is still going is started again.

    Calls: wait_for_run (list), id (list), url (view), watch (lost
    connection), state (in progress), watch, state (completed success).

    Args:
        tmp_path: Temporary directory (fixture).
    """
    answers = ["0 1", "0 4711", "0 https://example.invalid/runs/4711", "1 -",
               "0 in_progress ", "0 -", "0 completed success"]
    code, out, calls = bash(tmp_path, 'ci_run "1.2.14 CI 2/3" v1.2.14 push abcdef1 a b; '
                                      'rc=$?; ci_summary; exit "$rc"', answers)
    assert code == 0
    assert "watching interrupted, run is 'in_progress '" in out
    assert sum(call.startswith("run watch 4711") for call in calls) == 2
    assert "success" in out.split("CI runs on GitHub:")[1]


def test_red_run_is_not_retried(tmp_path: Path) -> None:
    """A completed red run fails at once, without a second watch.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    answers = ["0 1", "0 4711", "0 https://example.invalid/runs/4711", "1 -",
               "0 completed failure"]
    code, out, calls = bash(tmp_path, 'ci_run L v1 push abcdef1 a b PRURL', answers)
    assert code == 1 and "interrupted" not in out
    assert sum(call.startswith("pr checks") for call in calls) == 1
