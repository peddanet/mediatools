"""bundle_tests - skip the tests of unchanged bundle files (dev-conventions bundle).

Copy to tools/bundle_tests.py and load it from tests/conftest.py next to
pytest_progress:

    pytest_plugins = ["pytest_progress", "bundle_tests"]

A project copies some files of the dev-conventions bundle byte for byte
(tools/release.sh, tools/output.sh, tests/test_release_tools.py, ...).
tools/bundle.sha256 - manifests/<platform>.sha256 of the bundle, in the
format of sha256sum - lists them with their SHA-256. While every listed
file is unchanged, the listed test files only repeat what the
dev-conventions repository tests at its tag; they then take half of the
test time of a small project (session-snap 2.2.0: 56 of 126 s), locally and
in both CI runs. This plugin skips them with one note in the summary. As
soon as one listed file differs, is missing or the manifest is missing,
every test runs. CHECK_BUNDLE_TESTS=1 runs them anyway.

Check by hand: sha256sum -c tools/bundle.sha256
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

MANIFEST = Path("tools") / "bundle.sha256"


def listed_files(root: Path) -> list[str] | None:
    """Read the manifest and compare every listed file.

    Args:
        root: Project directory.

    Returns:
        The listed paths (relative, "/"-separated) when all match, else None.
    """
    manifest = root / MANIFEST
    if not manifest.is_file():
        return None
    names = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, name = line.split(maxsplit=1)
        name = name.lstrip("*")
        path = root / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            return None
        names.append(name)
    return names


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Mark the tests of unchanged bundle test files as skipped.

    Args:
        config: The pytest configuration.
        items: The collected tests.
    """
    if os.environ.get("CHECK_BUNDLE_TESTS") == "1":
        return
    root = Path(str(config.rootpath))
    names = listed_files(root)
    if not names:
        return
    tests = {name for name in names if name.startswith("tests/")}
    skip = pytest.mark.skip(reason="bundle files unchanged (tools/bundle.sha256); CHECK_BUNDLE_TESTS=1 runs them")
    for item in items:
        if item.nodeid.split("::", 1)[0] in tests:
            item.add_marker(skip)
