"""rsync invocation that preserves hard links and never deletes."""
from __future__ import annotations

import subprocess
from typing import Any

from mediatools.core.mediatypes import MediaTypes


class RsyncRunner:
    """Builds and runs the rsync command used by ``mediatools sync``.

    Options and why:

    ``-a``                 archive mode (recursion, times, permissions, symlinks)
    ``-H``                 hard links in the source stay hard links in the target
    ``-u``                 never overwrite a newer file on the receiving side
    ``--no-inc-recursive`` full file list first, so hard links are found before
                           data is copied (rsync manual recommendation)
    ``--min-size=1``       empty files are not transferred: a triage placeholder
                           never overwrites the full copy on the backup
    ``--exclude``          temporary shrink output and partial downloads

    ``--delete`` is deliberately **never** used: files that exist only in the
    target stay untouched and are reported instead.

    Attributes:
        source: Source directory with trailing slash.
        target: Target directory with trailing slash.
        rsync: Name or path of the rsync executable.
    """

    EXCLUDES = (f"{MediaTypes.SHRINK_TMP_DIR}/", "*.part", ".Trash-*/",
                ".mediatools-hl-*")
    BASE = ("-aHu", "--no-inc-recursive", "--min-size=1")

    def __init__(self, source: str, target: str, rsync: str = "rsync"):
        """Store the directories.

        Args:
            source: Source directory.
            target: Target directory.
            rsync: Name or path of the rsync executable.
        """
        # trailing slash: copy the content, not the directory itself
        self.source = source.rstrip("/") + "/"
        self.target = target.rstrip("/") + "/"
        self.rsync = rsync

    def command(self, dry_run: bool, log_file: str | None = None) -> list[str]:
        """Build the complete argument list.

        Args:
            dry_run: Add ``--dry-run`` and itemized output.
            log_file: rsync log file for the real run.

        Returns:
            Argument list for :func:`subprocess.run`.
        """
        cmd = [self.rsync, *self.BASE]
        cmd += [f"--exclude={pattern}" for pattern in self.EXCLUDES]
        if dry_run:
            cmd += ["--dry-run", "--itemize-changes"]
        else:
            cmd += ["--info=progress2,stats2", "--itemize-changes"]
            if log_file:
                cmd.append(f"--log-file={log_file}")
        return cmd + [self.source, self.target]

    def dry_run(self) -> dict[str, Any]:
        """Run rsync with ``--dry-run`` and summarise the changes.

        Returns:
            Result of :meth:`parse` plus ``returncode`` and ``stderr``.
        """
        res = subprocess.run(self.command(dry_run=True), capture_output=True, text=True,
                             errors="surrogateescape", check=False)
        summary = self.parse(res.stdout)
        summary["returncode"] = res.returncode
        summary["stderr"] = res.stderr.strip()
        return summary

    @staticmethod
    def parse(itemized: str) -> dict[str, Any]:
        """Count new files, updates, hard links and new directories.

        Itemize codes (rsync manual): ``>f+++++++++`` new file, ``>f.s......``
        changed file, ``hf`` hard link, ``cd+++++++++`` new directory.

        Args:
            itemized: stdout of an rsync run with ``--itemize-changes``.

        Returns:
            Counters ``new``, ``update``, ``hardlink``, ``newdir``, ``other``
            and up to ten example paths in ``samples_new``.
        """
        counts = {"new": 0, "update": 0, "hardlink": 0, "newdir": 0, "other": 0}
        samples: list[str] = []
        for line in itemized.splitlines():
            code = line[:11]
            if code.startswith(">f+++"):
                counts["new"] += 1
                if len(samples) < 10:
                    samples.append(line[12:])
            elif code.startswith(">f"):
                counts["update"] += 1
            elif code.startswith("hf"):
                counts["hardlink"] += 1
            elif code.startswith("cd+++"):
                counts["newdir"] += 1
            elif line.strip():
                counts["other"] += 1
        return {**counts, "samples_new": samples}

    def run(self, log_file: str | None = None) -> int:
        """Run the real transfer with progress on the terminal.

        Args:
            log_file: rsync log file.

        Returns:
            Exit code of rsync (0 ok, 24 vanished source files).
        """
        return subprocess.run(self.command(dry_run=False, log_file=log_file),
                              check=False).returncode
