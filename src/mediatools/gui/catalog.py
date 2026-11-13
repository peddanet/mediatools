"""The subcommands of the command line, read from its argument parser."""
from __future__ import annotations

import argparse

from mediatools.gui.commandspec import CommandSpec
from mediatools.gui.optionspec import OptionSpec


class CommandCatalog:
    """Reads the subcommands and their options from the argument parser.

    The GUI does not repeat any option of the command line: it reads them
    from the parser that ``mediatools`` itself uses. A new option in the
    command line therefore appears in the GUI without a change here.
    argparse offers no public interface for this, so the reading relies on
    its long-stable attributes ``_actions`` and ``_choices_actions``; the
    tests check every subcommand, so a change in argparse is noticed.

    Attributes:
        parser: The argument parser of ``mediatools``.
        program: Words that start a command line.
    """

    SKIP = ("attach", "front", "gui", "privacy")
    """Subcommands that the GUI replaces (watching), that start it, or that
    need a terminal (``privacy --edit`` opens a text editor; ``where`` shows
    the lists too)."""

    def __init__(self, parser: argparse.ArgumentParser, program: list[str] | None = None):
        """Remember the parser.

        Args:
            parser: The argument parser of ``mediatools``.
            program: Words that start a command line; ``["mediatools"]`` if None.
        """
        self.parser = parser
        self.program = program or ["mediatools"]

    @staticmethod
    def _subparsers(parser: argparse.ArgumentParser) -> argparse.Action:
        """Return the subcommand action of a parser.

        Args:
            parser: Main parser.

        Returns:
            The action that holds the subcommand parsers.

        Raises:
            ValueError: If the parser has no subcommands.
        """
        # pylint: disable=protected-access
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                return action
        raise ValueError("parser has no subcommands")

    @staticmethod
    def option(action: argparse.Action) -> OptionSpec | None:
        """Describe one parser action as a form field.

        Args:
            action: Action of a subcommand parser.

        Returns:
            The description, or None for help and hidden options.
        """
        if isinstance(action, argparse._HelpAction) \
                or action.help == argparse.SUPPRESS:      # pylint: disable=protected-access
            return None
        if isinstance(action, (argparse._StoreTrueAction,     # pylint: disable=protected-access
                               argparse._StoreConstAction)):  # pylint: disable=protected-access
            kind = "flag"
        elif isinstance(action, argparse._AppendAction):      # pylint: disable=protected-access
            kind = "list"
        elif action.choices:
            kind = "choice"
        elif action.type is int:
            kind = "int"
        elif action.type is float:
            kind = "float"
        else:
            kind = "text"
        flags = list(action.option_strings)
        return OptionSpec(dest=action.dest, flags=flags, help=action.help or "", kind=kind,
                          choices=[str(c) for c in action.choices or []],
                          default=None if kind == "flag" else action.default,
                          required=not flags and action.nargs is None,
                          path=kind == "text" and (action.dest in OptionSpec.PATH_NAMES
                                                   or (action.metavar or "") == "PLAN"))

    def commands(self) -> list[CommandSpec]:
        """Return all subcommands in the order of ``mediatools --help``.

        Returns:
            One description per subcommand; aliases and :attr:`SKIP` left out.
        """
        sub = self._subparsers(self.parser)
        helps = {choice.dest: choice.help or ""
                 for choice in getattr(sub, "_choices_actions", [])}
        seen: set[int] = set()
        result = []
        choices: dict[str, argparse.ArgumentParser] = dict(sub.choices or {})
        for name, parser in choices.items():
            if id(parser) in seen or name in self.SKIP:
                seen.add(id(parser))
                continue
            seen.add(id(parser))
            actions = parser._actions            # pylint: disable=protected-access
            options = [spec for spec in map(self.option, actions) if spec is not None]
            result.append(CommandSpec(name=name, help=helps.get(name, ""), options=options,
                                      program=list(self.program)))
        return result

    def command(self, name: str) -> CommandSpec:
        """Return one subcommand.

        Args:
            name: Subcommand name.

        Returns:
            Its description.

        Raises:
            KeyError: If there is no such subcommand.
        """
        for spec in self.commands():
            if spec.name == name:
                return spec
        raise KeyError(name)
