"""1.2.13 (workflow part): CI triggers and Python matrix.

The functions of the former ``apply-release.sh`` that followed CI runs moved
to ``tools/release.sh`` in 1.14.0 (tested in ``test_release_tools.py``).
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"


def test_workflow_triggers_and_matrix() -> None:
    """Only tags and pull requests start runs; the matrix holds the oldest and newest Python."""
    flow = WORKFLOW.read_text(encoding="utf-8")
    trigger = flow.split("\non:\n", 1)[1].split("\njobs:", 1)[0]
    assert trigger.split() == ["push:", "tags:", '["v*"]', "pull_request:", "branches:",
                               "[main,", "develop]", "workflow_dispatch:"]
    assert 'python: ["3.12", "3.14"]' in flow
    assert "actions/setup-python@v6" in flow and "setup-python@v5" not in flow
