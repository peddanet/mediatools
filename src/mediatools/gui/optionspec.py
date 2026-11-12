"""One argument of a subcommand, as the GUI shows and passes it."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class OptionSpec:
    """Describes one command-line argument for a form field.

    The GUI builds its forms from the argument parser of the command line
    (see :class:`~mediatools.gui.catalog.CommandCatalog`), so every option
    of the command line is available in the GUI and both always agree.

    Attributes:
        dest: Name of the parsed value, e.g. ``keep_copies``.
        flags: Option strings, e.g. ``["--keep-copies"]``; empty for a
            positional argument.
        help: Help text of the command line.
        kind: ``flag`` (on/off), ``choice``, ``int``, ``float``, ``text``
            or ``list`` (option that may be given several times).
        choices: Allowed values of a ``choice``.
        default: Default value of the command line, or None.
        required: A positional argument that must be given.
        path: The value is a file or directory (the form offers a browser).
    """

    PATH_NAMES = ("path", "source", "target", "existing", "undo", "downloads", "repo")

    dest: str
    flags: list[str] = field(default_factory=list)
    help: str = ""
    kind: str = "text"
    choices: list[str] = field(default_factory=list)
    default: object = None
    required: bool = False
    path: bool = False

    @property
    def positional(self) -> bool:
        """True for a positional argument."""
        return not self.flags

    @property
    def label(self) -> str:
        """Label of the form field: the long option or the argument name."""
        if self.positional:
            return self.dest
        longs = [flag for flag in self.flags if flag.startswith("--")]
        return (longs or self.flags)[0]

    def to_argv(self, value: object) -> list[str]:
        """Translate a form value into command-line words.

        Args:
            value: Value of the form field: bool for a flag, list for a
                ``list`` option, else text; empty text means "not given".

        Returns:
            The words to append, possibly none.

        Raises:
            ValueError: If a number field holds no number.
        """
        if self.kind == "flag":
            return [self.label] if value else []
        if self.kind == "list":
            items = value if isinstance(value, list) else str(value or "").splitlines()
            words: list[str] = []
            for item in (str(item).strip() for item in items):
                if item:
                    words += [self.label, item]
            return words
        text = str(value if value is not None else "").strip()
        if not text:
            return []
        if self.kind == "int":
            int(text)
        elif self.kind == "float":
            float(text)
        return [text] if self.positional else [self.label, text]

    def initial(self) -> object:
        """Return the value a new form shows.

        Returns:
            False for flags, an empty list for ``list`` options, else the
            default of the command line as text (empty if none).
        """
        if self.kind == "flag":
            return False
        if self.kind == "list":
            return []
        return "" if self.default is None else str(self.default)
