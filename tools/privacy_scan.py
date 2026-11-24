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
installed), the tracked file paths, all commit messages and the
identities: author and committer of every commit, tagger of every tag.
Matching ignores case; a term must not be part of a longer word (``cat``
does not match ``concatenate``).

Every e-mail address of an identity must be a no-reply address
(``*@users.noreply.github.com``, ``noreply@github.com``,
``noreply@anthropic.com``) or stand on its own line in the allow list:
commits made with a private global git identity would put a real address
into a public history (dev-conventions handover v8). Findings name the
commit or tag and show the address masked. A no-reply address and a name
equal to its login are not searched for terms: they are the public
pseudonym the conventions prescribe.

**A gate must never pass because its input is missing:** without any local
term the scan fails (exit 2). Only in CI (``CI=true``, always set by GitHub
Actions), which has no local list by design, it is skipped and says so.

Exit codes: 0 clean or skipped in CI, 1 terms or private addresses found,
2 no local terms, usage error or an old configuration (lists in
``~/scripts/mediatools.d``) still present.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from typing import Any

NO_REPLY = re.compile(r"^[^@\s]+@users\.noreply\.github\.com$|^noreply@github\.com$"
                      r"|^noreply@anthropic\.com$", re.IGNORECASE)
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
        self.allowed_addresses = {a.strip().lower() for a in allowed or [] if "@" in a}
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
    def old_lists() -> list[str]:
        """Find privacy lists in the folder of releases before 1.5.0.

        A gate must not pass silently because its input moved: while such a
        list exists (outside ``$MEDIATOOLS_HOME`` setups), the scan refuses.

        Returns:
            Paths of old lists; empty if none or ``$MEDIATOOLS_HOME`` is set.
        """
        data = PrivacyScan.datadir()
        if data.mode == "single":
            return []
        folder = data.legacy_dir()
        return [path for path in (os.path.join(folder, f"privacy_{kind}list.txt")
                                  for kind in ("deny", "allow")) if os.path.exists(path)]

    @staticmethod
    def local_terms() -> list[str]:
        """Read the deny list and the collection names.

        Returns:
            All local terms (unfiltered).
        """
        terms = PrivacyScan.read_list("privacy_denylist.txt")
        # pylint: disable-next=import-outside-toplevel
        from mediatools.core.config import Config
        # pylint: disable-next=import-outside-toplevel
        from mediatools.core.mediatypes import MediaTypes
        root = Config().path_value("collection", "root")
        if root and os.path.isdir(root):
            below_home = os.path.relpath(root, os.path.expanduser("~"))
            if not below_home.startswith(".."):
                terms += [part for part in below_home.split(os.sep)
                          if part.lower() not in STANDARD_DIRS]
            # folders mediatools creates itself (e.g. _broken) name no content
            terms += [entry.name for entry in os.scandir(root)
                      if entry.is_dir() and entry.name not in MediaTypes.SKIP_DIRS]
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

    @staticmethod
    def mask(address: str) -> str:
        """Hide an e-mail address except its shape.

        Args:
            address: The address.

        Returns:
            First letter of the local part and of the domain, the rest
            replaced, e.g. ``p***@g***.com``.
        """
        local, _, domain = address.partition("@")
        name, _, suffix = domain.rpartition(".")
        return f"{local[:1]}***@{name[:1]}***.{suffix}" if name else f"{local[:1]}***@***"

    def identities(self) -> list[tuple[str, str, str]]:
        """List the identities of the history.

        Returns:
            ``(location, name, address)`` for author and committer of every
            commit and the tagger of every annotated tag.
        """
        result: list[tuple[str, str, str]] = []
        log = self._git("log", "--all", "--format=%h%x00%an%x00%ae%x00%cn%x00%ce")
        for line in log.splitlines():
            parts = line.split("\0")
            if len(parts) == 5:
                result.append((f"commit {parts[0]} author", parts[1], parts[2]))
                result.append((f"commit {parts[0]} committer", parts[3], parts[4]))
        tags = self._git("for-each-ref", "refs/tags",
                         "--format=%(refname:short)%00%(taggername)%00%(taggeremail)")
        for line in tags.splitlines():
            parts = line.split("\0")
            if len(parts) == 3 and (parts[1] or parts[2]):
                result.append((f"tag {parts[0]} tagger", parts[1], parts[2].strip("<>")))
        return result

    def check_identities(self) -> None:
        """Search names and addresses for terms; demand no-reply addresses.

        A no-reply address is a pseudonym GitHub publishes for the account
        (``<id>+<login>@users.noreply.github.com``); it and a name equal to
        its login are the identity the conventions prescribe, so they are
        not searched for terms (a deny list may hold the login to keep it
        out of files).
        """
        for location, name, address in self.identities():
            if NO_REPLY.match(address):
                login = address.split("@", 1)[0].rpartition("+")[2].lower()
                if name.strip().lower() != login:
                    self._search(f"{location} name", name)
                continue
            self._search(f"{location} name", name)
            self._search(f"{location} e-mail", address)
            if address.lower() not in self.allowed_addresses:
                self.findings.append((location, f"not a no-reply address: {self.mask(address)}"))

    def run(self) -> int:
        """Scan files, paths, commit messages and identities.

        Returns:
            0 if clean (or skipped in CI), 1 if something was found, 2 if
            there are no local terms outside CI.
        """
        if self.pattern is None:
            if os.environ.get("CI") == "true":
                print("privacy scan skipped in CI: no local terms by design "
                      "(the deny list exists only on the developer's machine)")
                return 0
            print("privacy scan FAILED: no local terms - a scan without terms checks nothing.\n"
                  "  create the deny list from the sample (CONTRIBUTING.md, local files):\n"
                  "  mediatools privacy   (shows where the lists belong)", file=sys.stderr)
            return 2
        for path in self._git("ls-files", "-z").split("\0"):
            if not path:
                continue
            self._search(f"path {path}", path)
            text = self._text_of(path)
            if text is not None:
                self._search(path, text)
        log = self._git("log", "--all", "--format=commit %h%n%B")
        self._search("commit messages", log)
        self.check_identities()
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
    old = PrivacyScan.old_lists()
    if old:
        print("privacy scan refused: old configuration still present - "
              "move it first with: mediatools where --migrate", file=sys.stderr)
        for path in old:
            print(f"  {path}", file=sys.stderr)
        return 2
    return PrivacyScan(repo, PrivacyScan.local_terms(),
                       PrivacyScan.read_list("privacy_allowlist.txt")).run()


if __name__ == "__main__":
    sys.exit(main())
