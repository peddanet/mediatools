#!/usr/bin/env python3
"""Refuse a push that contains terms which allow conclusions about the data.

The terms are never part of the repository - a list of them would itself
reveal what it protects. They come from two local sources:

* ``$MEDIATOOLS_HOME/privacy_denylist.txt`` (default
  ``~/scripts/mediatools.d/privacy_denylist.txt``): one term per line,
  ``#`` starts a comment;
* the names of the unit directories directly below ``[collection] root``
  and the components of that path below the home directory (standard
  folders such as ``Pictures`` excepted).

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

STANDARD_DIRS = {"desktop", "documents", "downloads", "music", "pictures", "public",
                 "templates", "videos", "home", "projects", "scripts"}
MIN_LENGTH = 3


class PrivacyScan:
    """Collects local terms and searches the repository for them.

    Attributes:
        repo: Repository root.
        terms: Terms to search for (lower case).
        pattern: Compiled alternation of all terms, or None.
        findings: ``(location, term)`` pairs.
    """

    def __init__(self, repo: str, terms: list[str]):
        """Prepare the search.

        Args:
            repo: Repository root.
            terms: Terms to search for.
        """
        self.repo = repo
        self.terms = sorted({t.strip().lower() for t in terms if len(t.strip()) >= MIN_LENGTH},
                            key=len, reverse=True)
        self.pattern = re.compile(
            r"(?<![a-z0-9])(" + "|".join(re.escape(t) for t in self.terms) + r")(?![a-z0-9])",
            re.IGNORECASE) if self.terms else None
        self.findings: list[tuple[str, str]] = []

    @staticmethod
    def local_terms() -> list[str]:
        """Read the deny list and the collection names.

        Returns:
            All local terms (unfiltered).
        """
        home = os.environ.get("MEDIATOOLS_HOME", os.path.expanduser("~/scripts/mediatools.d"))
        terms: list[str] = []
        try:
            with open(os.path.join(home, "privacy_denylist.txt"), encoding="utf-8") as handle:
                terms += [line.split("#", 1)[0].strip() for line in handle]
        except FileNotFoundError:
            pass
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
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
        print(f"privacy scan: {len(self.terms)} term(s), {len(self.findings)} finding(s)")
        return 1 if self.findings else 0


def main() -> int:
    """Command-line entry point.

    Returns:
        Exit code.
    """
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    return PrivacyScan(repo, PrivacyScan.local_terms()).run()


if __name__ == "__main__":
    sys.exit(main())
