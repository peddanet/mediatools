"""The samples in examples/ stay in step with the code (repository completeness)."""
import configparser
import importlib.util
import os
from pathlib import Path

from mediatools.core.config import Config
from mediatools.gui.desktopentry import DesktopEntry

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
SPEC = importlib.util.spec_from_file_location("privacy_scan_examples",
                                              ROOT / "tools" / "privacy_scan.py")
assert SPEC and SPEC.loader
privacy_scan = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(privacy_scan)


def entries(path: Path) -> list[str]:
    """Read a list the way the privacy scan does.

    Args:
        path: List file.

    Returns:
        Entries without comments and empty lines.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    return [entry for entry in (line.split("#", 1)[0].strip() for line in lines) if entry]


def test_config_sample_is_the_built_in_template() -> None:
    """``config.ini.sample`` equals the template and parses."""
    sample = (EXAMPLES / "config.ini.sample").read_text(encoding="utf-8")
    assert sample == Config.TEMPLATE
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(sample)
    assert parser.has_section("collection") and parser.has_section("shrink")


def test_privacy_samples_hold_invented_terms() -> None:
    """Both lists have entries the scan accepts; no real path or address."""
    deny = entries(EXAMPLES / "privacy" / "denylist.sample.txt")
    allow = entries(EXAMPLES / "privacy" / "allowlist.sample.txt")
    assert deny and all(len(term) >= privacy_scan.MIN_LENGTH for term in deny)
    assert allow and not any("@" in phrase for phrase in allow)
    scan = privacy_scan.PrivacyScan(str(ROOT), deny, allow)
    assert scan.terms and scan.allowed_count == len(allow)


def test_run_time_files_are_package_files() -> None:
    """Icons and templates of entry and unit lie in the package (not generated)."""
    data = Path(DesktopEntry.ICON_DIR)
    for name in ("mediatools.svg", "mediatools-256.png", "mediatools-64.png",
                 "mediatools.desktop.in", "mediatools-snapshot.service.in"):
        assert (data / name).is_file(), name
    assert os.path.samefile(DesktopEntry.TEMPLATE, data / "mediatools.desktop.in")
    preview = ROOT / "docs" / "images" / "social-preview.png"
    assert preview.read_bytes()[16:24] == (1280).to_bytes(4, "big") + (640).to_bytes(4, "big")
    assert preview.stat().st_size < 1024 * 1024
    assert (ROOT / "docs" / "images" / "social-preview.svg").is_file()
