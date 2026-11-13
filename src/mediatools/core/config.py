"""Local configuration file ``<config folder>/mediatools.ini``."""
from __future__ import annotations

import configparser
import os

from mediatools.core.datadir import DataDir


class Config:
    """Machine-specific settings kept outside the repository.

    Everything that describes a concrete collection - its location, the
    name of the shrink databases, folder names worth abbreviating, the
    backup disk - lives in this file, so the code and its history allow no
    conclusions about the data. The file is plain INI (``configparser``);
    a commented template is written by ``mediatools config --init``.

    Attributes:
        path: Absolute path of the INI file.
        parser: The loaded :class:`configparser.ConfigParser`.
    """

    FILE = DataDir.CONFIG_FILE
    DEFAULT_DB_NAME = "shrink.db"

    TEMPLATE = """\
# mediatools configuration. Lines starting with # are comments.
# Paths may use ~ for the home directory.

[collection]
# Master directory of the media collection; its sub-directories are units.
# Used whenever a command is called without a path. A path given on the
# command line always wins. Change it with:
#   mediatools config --set collection.root=~/other/collection
root =

[sync]
# Default source of "mediatools sync" (e.g. your home directory);
# empty = [collection] root.
source =

[shrink]
# File name of the per-directory database of shrunk videos.
db_name = shrink.db
# Videos encoded at the same time when --jobs is not given (1 = one after
# the other). Compare the MB/s of the progress line with 1 and 2.
jobs = 1
# libx265 preset when --preset is not given: faster encodes about 1.5 times
# as fast at nearly the same quality (measure with bench-encode.py first);
# slower compresses more. Default medium.
preset = medium

[flatten]
# Folder abbreviations used when a flat name is too long (naming level 1).
# Format: Name=Abbreviation, comma separated.
abbreviations = Videos=V, Images=I, Audios=U

[snapshot]
# Directory that is backed up (normally the same as [collection] root or
# your home directory).
source =
# Mount point of the backup disk (must be ext4 or btrfs).
disk =
# Directory on the disk that holds the snapshots (relative to disk).
root = rsnapshot
# Number of snapshots kept per level. alpha: every run; beta: weekly;
# gamma: monthly; delta: yearly.
retain_alpha = 7
retain_beta = 4
retain_gamma = 12
retain_delta = 2
# rsync exclude patterns, comma separated (temporary shrink output etc.).
exclude = newShrinkedFiles/
# A run is aborted if it would remove more than this many files from the
# newest snapshot (they stay in older snapshots, but a mistake in the
# source path would otherwise empty the mirror).
max_deletions = 500

[gui]
# Text size of the window (1.0 = 10 pt; 0.8 to 2.5). Ctrl++ / Ctrl+- / Ctrl+0
# in the window change it and save it here.
scale = 1.0
# Colour scheme: auto (follow the desktop, also its accent colour), light
# or dark (menu View).
theme = auto
# yes: put back the application menu entry if it is missing (set by
# "mediatools gui --install-desktop").
desktop_entry =
"""

    def __init__(self, datadir: DataDir | None = None):
        """Load the configuration; a missing file yields the defaults.

        Args:
            datadir: Data directory; the default one if None.
        """
        self.path = (datadir or DataDir()).config_file
        self.parser = configparser.ConfigParser(interpolation=None)
        setattr(self.parser, "optionxform", str)       # keep the case of keys
        self.parser.read_string(self.TEMPLATE)
        if os.path.exists(self.path):
            self.parser.read(self.path, encoding="utf-8")

    # -- file ------------------------------------------------------------
    def exists(self) -> bool:
        """Tell whether the INI file exists.

        Returns:
            True if the file exists.
        """
        return os.path.exists(self.path)

    def write_template(self) -> bool:
        """Write the commented template unless the file already exists.

        Returns:
            True if the file was written, False if it already existed.
        """
        if self.exists():
            return False
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write(self.TEMPLATE)
        return True

    def set_value(self, section: str, key: str, value: str) -> None:
        """Set one value in the INI file, keeping comments and layout.

        The file is created from the template if it does not exist. Only
        the line ``key = ...`` inside the section is replaced (or added at
        the end of the section).

        Args:
            section: INI section.
            key: Key inside the section.
            value: New value.
        """
        self.write_template()
        with open(self.path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        current, start, end, hit = None, None, len(lines), None
        for number, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                if current == section:
                    end = number
                    break
                current = stripped[1:-1].strip()
                if current == section:
                    start = number
            elif current == section and stripped.split("=", 1)[0].strip() == key \
                    and not stripped.startswith(("#", ";")):
                hit = number
        entry = f"{key} = {value}"
        if hit is not None:
            lines[hit] = entry
        elif start is not None:
            insert = end
            while insert > start + 1 and not lines[insert - 1].strip():
                insert -= 1
            lines.insert(insert, entry)
        else:
            lines += ["", f"[{section}]", entry]
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        if not self.parser.has_section(section):
            self.parser.add_section(section)
        self.parser.set(section, key, value)

    # -- values ----------------------------------------------------------
    def get(self, section: str, key: str) -> str:
        """Return a raw value.

        Args:
            section: INI section.
            key: Key inside the section.

        Returns:
            Stripped value; empty string if unset.
        """
        return self.parser.get(section, key, fallback="").strip()

    def path_value(self, section: str, key: str) -> str | None:
        """Return a path value with ``~`` expanded.

        Args:
            section: INI section.
            key: Key inside the section.

        Returns:
            Absolute path, or None if the value is empty.
        """
        value = self.get(section, key)
        return os.path.abspath(os.path.expanduser(value)) if value else None

    def integer(self, section: str, key: str) -> int:
        """Return an integer value.

        Args:
            section: INI section.
            key: Key inside the section.

        Returns:
            The value.

        Raises:
            SystemExit: If the value is not an integer.
        """
        value = self.get(section, key)
        try:
            return int(value)
        except ValueError:
            raise SystemExit(f"Error: [{section}] {key} must be an integer, "
                             f"found {value!r} in {self.path}") from None

    def db_name(self) -> str:
        """Return the file name of the shrink databases.

        Returns:
            File name without directory.

        Raises:
            SystemExit: If the value contains a path separator.
        """
        name = self.get("shrink", "db_name") or self.DEFAULT_DB_NAME
        if os.sep in name:
            raise SystemExit(f"Error: [shrink] db_name must be a file name: {name!r}")
        return name

    def abbreviations(self) -> dict[str, str]:
        """Return the folder abbreviations of naming level 1.

        Returns:
            Mapping folder name to abbreviation.

        Raises:
            SystemExit: If an entry is not of the form ``Name=Abbreviation``.
        """
        result: dict[str, str] = {}
        for item in self.get("flatten", "abbreviations").split(","):
            item = item.strip()
            if not item:
                continue
            name, sep, short = (part.strip() for part in item.partition("="))
            if not sep or not name or not short:
                raise SystemExit(f"Error: [flatten] abbreviations: bad entry {item!r}; "
                                 "expected Name=Abbreviation")
            result[name] = short
        return result

    def require_path(self, section: str, key: str, given: str | None) -> str:
        """Return a command-line path or, if omitted, the configured one.

        Args:
            section: INI section of the fallback value.
            key: Key of the fallback value.
            given: Path from the command line or None.

        Returns:
            Path to use (not yet validated).

        Raises:
            SystemExit: If neither is available; the message explains how
                to configure the value.
        """
        if given:
            return given
        value = self.path_value(section, key)
        if value is None:
            raise SystemExit(f"Error: no path given and [{section}] {key} is not set.\n"
                             f"Either pass the path or set it in {self.path}\n"
                             "(create the file with: mediatools config --init)")
        return value

    def render(self) -> str:
        """Return the effective configuration as INI text.

        Returns:
            All sections and values, defaults included.
        """
        lines = []
        for section in self.parser.sections():
            lines.append(f"[{section}]")
            lines.extend(f"{key} = {value}" for key, value in self.parser.items(section))
            lines.append("")
        return "\n".join(lines)
