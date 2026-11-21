"""Release 1.5.1: shorter CI (a patch that does not fit: see test_apply_release.py)."""
from test_release_1213 import WORKFLOW


def test_tag_runs_only_release_check_and_pip_is_cached() -> None:
    """The gates skip tag pushes; release-check no longer waits for them."""
    flow = WORKFLOW.read_text(encoding="utf-8")
    gates = flow.split("\n  gates:\n", 1)[1].split("\n  release-check:\n", 1)[0]
    release = flow.split("\n  release-check:\n", 1)[1]
    assert "if: github.ref_type != 'tag'" in gates
    assert "cache: pip" in gates and "cache-dependency-path: pyproject.toml" in gates
    assert "needs:" not in release
    assert "actions/checkout@v5" in flow and "checkout@v4" not in flow
