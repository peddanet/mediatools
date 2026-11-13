#!/usr/bin/env python3
"""Refuse a push that contains terms which allow conclusions about the data.

The terms are never part of the repository - a list of them would itself
reveal what it protects. They come from two local sources:

* the deny list ``~/.config/mediatools/privacy/denylist.txt`` (shown by
  ``mediatools privacy``; ``$MEDIATOOLS_HOME/privacy_denylist.txt`` when
  that variable is set): one term per line, ``#`` starts a comment;
* the names of the unit directories directly below ``[collection] root``
  and the components of that path below the home directory (standard
  folders such as ``Pictures`` excepted).

Allowed phrases come from the allow list next to it (same format). They
are blanked out before the search, so a listed term stays forbidden
everywhere except inside an allowed phrase - e.g. the term ``level`` with
the allowed phrase ``sea level``.

Scanned are the contents of all tracked files (PDFs via ``pdftotext`` if
installed), the tracked file paths and all commit messages. Matching
ignores case; a term must not be part of a longer word (``cat`` does not
match ``concatenate``). Without any local term the scan is skipped, so the
check is neutral in CI.

Exit codes: 0 clean or skipped, 1 terms found, 2 usage error.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from typing import Any

STANDARD_DIRS = {"desktop", "documents", "downloads", "music", "pictures", "public",
                 "templates", "videos", "home", "projects", "scripts"}
MIN_LENGTH = 3


class PrivacyScan:
    """Collects local terms and searches the repository for them.

    Attributes:
        repo: Repository root.
        terms: Terms to search for (lower case).
        pattern: Compiled alternation of all terms, or None.
        allowed: Compiled alternation of the allowed phrases, or None.
        allowed_count: Number of allowed phrases.
        findings: ``(location, term)`` pairs.
    """

    def __init__(self, repo: str, terms: list[str], allowed: list[str] | None = None):
        """Prepare the search.

        Args:
            repo: Repository root.
            terms: Terms to search for.
            allowed: Phrases in which a term is permitted.
        """
        self.repo = repo
        self.terms = sorted({t.strip().lower() for t in terms if len(t.strip()) >= MIN_LENGTH},
                            key=len, reverse=True)
        self.pattern = re.compile(
            r"(?<![a-z0-9])(" + "|".join(re.escape(t) for t in self.terms) + r")(?![a-z0-9])",
            re.IGNORECASE) if self.terms else None
        phrases = sorted({a.strip() for a in allowed or [] if a.strip()}, key=len, reverse=True)
        self.allowed = re.compile("|".join(self._phrase(a) for a in phrases),
                                  re.IGNORECASE) if phrases else None
        self.allowed_count = len(phrases)
        self.findings: list[tuple[str, str]] = []

    @staticmethod
    def _phrase(text: str) -> str:
        """Build the regular expression of one allowed phrase.

        Word boundaries are required only where the phrase begins or ends
        with a letter or digit, so ``top-level`` does not cover ``stop-level``
        while ``key=`` may be followed by anything.

        Args:
            text: Allowed phrase.

        Returns:
            Regular expression.
        """
        start = r"(?<![a-z0-9])" if text[0].isalnum() else ""
        end = r"(?![a-z0-9])" if text[-1].isalnum() else ""
        return start + re.escape(text) + end

    @staticmethod
    def datadir() -> Any:
        """Return the folders of mediatools from the source tree of this clone.

        Returns:
            A ``mediatools.core.datadir.DataDir``.
        """
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
        from mediatools.core.datadir import DataDir  # pylint: disable=import-outside-toplevel
        return DataDir()

    @staticmethod
    def read_list(name: str) -> list[str]:
        """Read a local list of the configuration.

        Args:
            name: ``privacy_denylist.txt`` or ``privacy_allowlist.txt`` (mapped
                to the deny or allow list wherever it lives), or another file
                name in the configuration folder.

        Returns:
            Entries without comments and empty lines; empty if the file is missing.
        """
        data = PrivacyScan.datadir()
        kinds = {"privacy_denylist.txt": "deny", "privacy_allowlist.txt": "allow"}
        path = (data.privacy_file(kinds[name]) if name in kinds
                else os.path.join(data.config_dir, name))
        try:
            with open(path, encoding="utf-8") as handle:
                return [entry for entry in (line.split("#", 1)[0].strip() for line in handle)
                        if entry]
        except FileNotFoundError:
            return []

    @staticmethod
    def local_terms() -> list[str]:
        """Read the deny list and the collection names.

        Returns:
            All local terms (unfiltered).
        """
        terms = PrivacyScan.read_list("privacy_denylist.txt")
        from mediatools.core.config import Config  # pylint: disable=import-outside-toplevel
        root = Config().path_value("collection", "root")
        if root and os.path.isdir(root):
            below_home = os.path.relpath(root, os.path.expanduser("~"))
            if not below_home.startswith(".."):
                terms += [part for part in below_home.split(os.sep)
                          if part.lower() not in STANDARD_DIRS]
            terms += [entry.name for entry in os.scandir(root) if entry.is_dir()]
        return [term for term in terms if term]

    def _git(self, *args: str) -> str:
        """Run a git command in the repository.

        Args:
            *args: git arguments.

        Returns:
            Standard output.
        """
        git = shutil.which("git") or "git"
        return subprocess.run([git, "-C", self.repo, *args], capture_output=True, text=True,
                              errors="surrogateescape", check=True).stdout

    def _text_of(self, path: str) -> str | None:
        """Return the searchable text of a tracked file.

        Args:
            path: Path relative to the repository.

        Returns:
            Text, or None for binary files that cannot be converted.
        """
        full = os.path.join(self.repo, path)
        with open(full, "rb") as handle:
            data = handle.read()
        if path.lower().endswith(".pdf"):
            tool = shutil.which("pdftotext")
            if tool is None:
                print(f"warning: pdftotext missing, not scanned: {path}", file=sys.stderr)
                return None
            return subprocess.run([tool, full, "-"], capture_output=True, text=True,
                                  errors="replace", check=False).stdout
        if b"\0" in data:
            return None
        return data.decode("utf-8", errors="replace")

    def _search(self, location: str, text: str) -> None:
        """Record every term found in a text.

        Args:
            location: Where the text comes from.
            text: Text to search.
        """
        if self.pattern is None:
            return
        for number, line in enumerate(text.splitlines(), 1):
            if self.allowed is not None:
                line = self.allowed.sub(lambda m: " " * len(m.group(0)), line)
            for match in self.pattern.finditer(line):
                self.findings.append((f"{location}:{number}", match.group(1)))

    def run(self) -> int:
        """Scan files, paths and commit messages.

        Returns:
            0 if clean or skipped, 1 if terms were found.
        """
        if self.pattern is None:
            print("privacy scan skipped: no local terms "
                  "(privacy_denylist.txt / [collection] root)")
            return 0
        for path in self._git("ls-files", "-z").split("\0"):
            if not path:
                continue
            self._search(f"path {path}", path)
            text = self._text_of(path)
            if text is not None:
                self._search(path, text)
        log = self._git("log", "--all", "--format=commit %h%n%B")
        self._search("commit messages", log)
        for location, term in self.findings:
            print(f"{location}: {term}")
        print(f"privacy scan: {len(self.terms)} term(s), {self.allowed_count} allowed phrase(s), "
              f"{len(self.findings)} finding(s)")
        return 1 if self.findings else 0


def main() -> int:
    """Command-line entry point.

    Returns:
        Exit code.
    """
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    return PrivacyScan(repo, PrivacyScan.local_terms(),
                       PrivacyScan.read_list("privacy_allowlist.txt")).run()


if __name__ == "__main__":
    sys.exit(main())
