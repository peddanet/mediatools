"""Release 1.5.1: shorter CI and a clean stop when a patch does not fit."""
import subprocess
import tarfile
from pathlib import Path

from test_apply_release import SCRIPT, fixture_setup, git  # pylint: disable=unused-import
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


def test_patch_that_does_not_fit_leaves_a_clean_clone(
        setup: dict[str, Path], tmp_path: Path) -> None:
    """A failing ``git am`` is aborted, the branch removed, the reason printed.

    Args:
        setup: Clone at 1.2.5 and download folder (fixture of test_apply_release).
        tmp_path: Temporary directory (fixture).
    """
    home, repo = setup["home"], setup["repo"]
    (home / ".venvs" / "mediatools" / "bin").mkdir(parents=True)
    (home / ".venvs" / "mediatools" / "bin" / "activate").write_text("", encoding="utf-8")
    git("branch", "-q", "--set-upstream-to=origin/main", "main", cwd=repo)
    # a patch made on 1.2.6 for 1.2.7 - 1.2.6 itself is missing
    patch_dir = tmp_path / "mediatools-hotfix-1.2.7-patch"
    patch_dir.mkdir()
    (patch_dir / "0001-Release-1.2.7.patch").write_text(
        "From 0000000000000000000000000000000000000000 Mon Sep 17 00:00:00 2001\n"
        "From: t <t@example.invalid>\nDate: Fri, 13 Nov 2026 12:00:00 +0100\n"
        "Subject: [PATCH] Release 1.2.7\n\n---\n"
        "diff --git a/src/mediatools/__init__.py b/src/mediatools/__init__.py\n"
        "--- a/src/mediatools/__init__.py\n+++ b/src/mediatools/__init__.py\n"
        "@@ -1 +1 @@\n-__version__ = \"1.2.6\"\n+__version__ = \"1.2.7\"\n--\n2.43.0\n",
        encoding="utf-8")
    with tarfile.open(home / "Downloads" / "mediatools-hotfix-1.2.7-patch.tar.gz", "w:gz") as tar:
        tar.add(patch_dir, arcname=patch_dir.name)
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "APPLY_RELEASE_ALLOW_ROOT": "1",
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    result = subprocess.run(["bash", str(SCRIPT), "-y", "--repo", str(repo)], env=env,
                            capture_output=True, text=True, check=False)
    assert result.returncode == 1
    assert "does not fit onto main (released: 1.2.5)" in result.stdout
    assert "earlier release is missing" in result.stdout
    status = subprocess.run(["git", "status", "--porcelain=v2", "--branch"], cwd=repo,
                            capture_output=True, text=True, check=True).stdout
    assert "# branch.head main" in status
    assert not (repo / ".git" / "rebase-apply").exists()
    branches = subprocess.run(["git", "branch", "--list", "hotfix/*"], cwd=repo,
                              capture_output=True, text=True, check=True).stdout
    assert branches == ""
