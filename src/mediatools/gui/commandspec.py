"""One subcommand with its arguments, as the GUI shows and runs it."""
from __future__ import annotations

import shlex
from dataclasses import dataclass, field

from mediatools.gui.optionspec import OptionSpec


@dataclass
class CommandSpec:
    """A subcommand of the command line and the form that runs it.

    Attributes:
        name: Subcommand, e.g. ``shrink``.
        help: Short help of the subcommand.
        options: Its arguments in command-line order (positionals first).
        program: Words that start the command line, e.g. ``["mediatools"]``.
    """

    READ_ONLY = ("progress", "status", "savings")
    """Commands that never change anything (run without confirmation)."""

    CHANGE_FLAGS = {"where": "--migrate"}
    """Commands that change something only with the given option."""

    PREVIEW_FLAGS = ("--dry-run", "--list", "--report-only", "--path")
    """Options that turn a command into a read-only preview."""

    name: str
    help: str = ""
    options: list[OptionSpec] = field(default_factory=list)
    program: list[str] = field(default_factory=lambda: ["mediatools"])

    def option(self, label: str) -> OptionSpec | None:
        """Return the argument with a label, e.g. ``--dry-run``.

        Args:
            label: Long option or positional name.

        Returns:
            The argument, or None if the command has none of that name.
        """
        return next((spec for spec in self.options if spec.label == label), None)

    def argv(self, values: dict[str, object]) -> list[str]:
        """Build the arguments after the program name.

        Args:
            values: Form values by ``dest``; missing ones count as not given.

        Returns:
            Subcommand and its words, e.g. ``["shrink", "/data", "--crf", "26"]``.

        Raises:
            ValueError: If a required argument is missing or a number is invalid.
        """
        words = [self.name]
        for spec in sorted(self.options, key=lambda spec: not spec.positional):
            try:
                part = spec.to_argv(values.get(spec.dest, spec.initial()))
            except ValueError as exc:
                raise ValueError(f"{spec.label}: not a number") from exc
            if spec.required and not part:
                raise ValueError(f"{spec.label} is required")
            words += part
        return words

    def command_line(self, values: dict[str, object]) -> str:
        """Return the equivalent shell command, quoted for copying.

        Args:
            values: Form values by ``dest``.

        Returns:
            Text such as ``mediatools shrink /data --crf 26``.
        """
        return shlex.join(self.program + self.argv(values))

    def changes_data(self, values: dict[str, object]) -> bool:
        """Tell whether a run may change files (and needs a confirmation).

        Args:
            values: Form values by ``dest``.

        Returns:
            False for read-only commands, previews and ``where`` without
            ``--migrate``, else True.
        """
        if self.name in self.READ_ONLY:
            return False
        if self.name in self.CHANGE_FLAGS:
            spec = self.option(self.CHANGE_FLAGS[self.name])
            return spec is not None and bool(values.get(spec.dest))
        for flag in self.PREVIEW_FLAGS:
            spec = self.option(flag)
            if spec is not None and values.get(spec.dest):
                return False
        return True
