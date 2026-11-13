"""Selection of an earlier shrink job for a plain ``mediatools shrink``."""
from __future__ import annotations

from collections.abc import Callable

from mediatools.shrink.jobregistry import JobRegistry


class JobChooser:
    """Lists the remembered jobs and lets the user pick one to continue.

    Used when ``mediatools shrink`` is called without any argument on a
    terminal and no shrink runs: instead of silently starting the default
    job, the jobs run so far are listed with their settings and progress,
    the most recently active first. The answer is a number (continue that
    job), ``n`` (new job with the defaults) or ``q`` (end).

    Attributes:
        registry: Remembered jobs.
        ask: Reads one answer (``input``).
        say: Prints one line (``print``).
        limit: Most jobs listed.
    """

    def __init__(self, registry: JobRegistry, ask: Callable[[str], str] | None = None,
                 say: Callable[[str], None] | None = None, limit: int = 9):
        """Prepare the selection.

        Args:
            registry: Remembered jobs.
            ask: Reads one answer; ``input`` if None.
            say: Prints one line; ``print`` if None.
            limit: Most jobs listed.
        """
        self.registry = registry
        self.ask = ask or input
        self.say = say or print
        self.limit = limit

    def choose(self) -> list[str] | None:
        """Show the jobs and read the choice.

        Returns:
            Arguments of the chosen job (``["shrink", root, ...]``), an empty
            list for a new job with the defaults, or None to end. With no
            remembered job: an empty list without asking.
        """
        entries = self.registry.entries()[:self.limit]
        if not entries:
            return []
        self.say("Shrink jobs so far (most recent first):")
        for number, entry in enumerate(entries, 1):
            self.say(f"  {number}  {JobRegistry.line(entry)}")
        self.say("  n  new job with the defaults")
        self.say("  q  quit")
        while True:
            try:
                answer = self.ask("Continue which job? [1] ").strip().lower()
            except EOFError:
                return None
            if answer in ("", "1") or (answer.isdigit() and 1 <= int(answer) <= len(entries)):
                return entries[int(answer or "1") - 1].argv()
            if answer == "n":
                return []
            if answer == "q":
                return None
            self.say(f"please answer 1 to {len(entries)}, n or q")
