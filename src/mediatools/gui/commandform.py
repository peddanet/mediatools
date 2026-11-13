"""Form of one subcommand: a field per option, run, preview, copy."""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from mediatools.gui.commandspec import CommandSpec
from mediatools.gui.consoleview import ConsoleView
from mediatools.gui.formstore import FormStore
from mediatools.gui.optionspec import OptionSpec


class CommandForm:
    """Shows every option of a subcommand as a field and runs the command.

    Fields: a check box for on/off options, a list for options with fixed
    values, an entry for text and numbers (with a browse button for files
    and folders), a text box with one value per line for options that may
    be given several times. The command line that will run is always shown
    and can be copied. *Preview* runs the command with its read-only option
    (``--dry-run``, ``--list``, ``--report-only``). A command that may
    change files runs only after a confirmation that shows its command line.
    The values of the last run are remembered (:class:`FormStore`).

    Attributes:
        frame: Container of the widgets (place it in the parent).
        spec: The subcommand.
        console: Output pane.
        store: Remembered values, or None.
        variables: Tk variables (or text boxes) by ``dest``.
        line: Text of the command-line preview.
        ask: Confirmation dialog, replaceable in tests.
    """

    def __init__(self, master: tk.Misc, spec: CommandSpec, console: ConsoleView,
                 store: FormStore | None = None,
                 ask: Callable[[str, str], bool] = messagebox.askyesno):
        """Build the form.

        Args:
            master: Parent widget.
            spec: The subcommand.
            console: Output pane that runs the command.
            store: Remembered values, or None.
            ask: Confirmation dialog (title, text) -> yes.
        """
        self.frame = ttk.Frame(master)
        self.spec = spec
        self.console = console
        self.store = store
        self.ask = ask
        self.variables: dict[str, tk.Variable | tk.Text] = {}
        self.line = tk.StringVar()
        ttk.Label(self.frame, text=spec.help, wraplength=900, justify="left").grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 6))
        remembered = store.load(spec.name) if store else {}
        for row, option in enumerate(spec.options, start=1):
            self._field(row, option, remembered.get(option.dest, option.initial()))
        row = len(spec.options) + 1
        ttk.Entry(self.frame, textvariable=self.line, state="readonly", font="TkFixedFont").grid(
            row=row, column=0, columnspan=4, sticky="we", pady=(8, 4))
        toolbar = ttk.Frame(self.frame)
        toolbar.grid(row=row + 1, column=0, columnspan=4, sticky="w")
        ttk.Button(toolbar, text="Run", style="Accent.TButton",
                   command=self.run).pack(side="left")
        if self.preview_option() is not None:
            ttk.Button(toolbar, text="Preview (" + str(self.preview_option()) + ")",
                       command=self.preview).pack(side="left", padx=4)
        ttk.Button(toolbar, text="Copy command", command=self.copy).pack(side="left", padx=4)
        ttk.Button(toolbar, text="Defaults", command=self.reset).pack(side="left")
        self.frame.columnconfigure(1, weight=1)
        self.update_line()

    def _field(self, row: int, option: OptionSpec, value: object) -> None:
        """Add the field of one option.

        Args:
            row: Grid row.
            option: The option.
            value: Initial value.
        """
        ttk.Label(self.frame, text=option.label + (" *" if option.required else "")).grid(
            row=row, column=0, sticky="nw", padx=(0, 8), pady=1)
        widget: tk.Widget
        if option.kind == "flag":
            var: tk.Variable = tk.BooleanVar(value=bool(value))
            widget = ttk.Checkbutton(self.frame, variable=var)
        elif option.kind == "list":
            box = tk.Text(self.frame, height=2, width=40, font="TkFixedFont")
            items = value if isinstance(value, list) else []
            box.insert("1.0", "\n".join(str(item) for item in items))
            box.bind("<KeyRelease>", lambda _event: self.update_line())
            box.grid(row=row, column=1, sticky="we", pady=1)
            self.variables[option.dest] = box
            self._help(row, option)
            return
        elif option.kind == "choice":
            var = tk.StringVar(value=str(value or ""))
            values = option.choices if option.required else [""] + option.choices
            widget = ttk.Combobox(self.frame, textvariable=var, values=values, state="readonly")
        else:
            var = tk.StringVar(value=str(value or ""))
            widget = ttk.Entry(self.frame, textvariable=var)
        widget.grid(row=row, column=1, sticky="we" if option.kind != "flag" else "w", pady=1)
        var.trace_add("write", lambda *_args: self.update_line())
        self.variables[option.dest] = var
        if option.path:
            ttk.Button(self.frame, text="...", width=3,
                       command=lambda: self.browse(option)).grid(row=row, column=2, padx=2)
        self._help(row, option)

    def _help(self, row: int, option: OptionSpec) -> None:
        """Show the help text of an option next to its field.

        Args:
            row: Grid row.
            option: The option.
        """
        known = option.default in (None, "", False) or "default" in option.help
        text = option.help + ("" if known else f"  (default {option.default})")
        ttk.Label(self.frame, text=text, style="Hint.TLabel", wraplength=520,
                  justify="left").grid(row=row, column=3, sticky="w", padx=6)

    def values(self) -> dict[str, object]:
        """Return the current field values.

        Returns:
            Values by ``dest``.
        """
        result: dict[str, object] = {}
        for dest, var in self.variables.items():
            if isinstance(var, tk.Text):
                result[dest] = [item for item in var.get("1.0", "end").splitlines() if item.strip()]
            else:
                result[dest] = var.get()
        return result

    def set_value(self, dest: str, value: object) -> None:
        """Set one field (used by other panels, e.g. a plan for undo).

        Args:
            dest: Field name.
            value: New value.
        """
        var = self.variables[dest]
        if isinstance(var, tk.Text):
            var.delete("1.0", "end")
            var.insert("1.0", "\n".join(map(str, value if isinstance(value, list) else [value])))
        else:
            var.set(value)
        self.update_line()

    def update_line(self) -> None:
        """Show the command line of the current values."""
        try:
            self.line.set(self.spec.command_line(self.values()))
        except ValueError as exc:
            self.line.set("incomplete: " + str(exc))

    def preview_option(self) -> str | None:
        """Return the read-only option of this command.

        Returns:
            ``--dry-run``, ``--list`` or ``--report-only``, or None.
        """
        for flag in ("--dry-run", "--list", "--report-only"):
            if self.spec.option(flag) is not None:
                return flag
        return None

    def browse(self, option: OptionSpec) -> None:
        """Choose a file or folder for a path field.

        Args:
            option: The path option.
        """
        var = self.variables[option.dest]
        if isinstance(var, tk.Text):
            return
        start = str(var.get() or "") or None
        if option.dest == "undo":
            chosen = filedialog.askopenfilename(parent=self.frame, initialdir=start)
        else:
            chosen = filedialog.askdirectory(parent=self.frame, initialdir=start)
        if chosen:
            var.set(chosen)

    def copy(self) -> None:
        """Copy the command line to the clipboard."""
        self.frame.clipboard_clear()
        self.frame.clipboard_append(self.line.get())

    def reset(self) -> None:
        """Set all fields to the defaults of the command line."""
        for option in self.spec.options:
            self.set_value(option.dest, option.initial())

    def preview(self) -> bool:
        """Run the command with its read-only option.

        Returns:
            True if the command was started.
        """
        values = self.values()
        option = self.spec.option(self.preview_option() or "")
        if option is not None:
            values[option.dest] = True
        return self._start(values)

    def run(self) -> bool:
        """Run the command with the current values.

        Returns:
            True if the command was started.
        """
        return self._start(self.values())

    def _start(self, values: dict[str, object]) -> bool:
        """Check, confirm, remember and start.

        Args:
            values: Values by ``dest``.

        Returns:
            True if the command was started.
        """
        try:
            args = self.spec.argv(values)
            shown = self.spec.command_line(values)
        except ValueError as exc:
            self.console.state.set("not started: " + str(exc))
            return False
        if self.spec.changes_data(values) and not self.ask(
                "Run mediatools " + self.spec.name,
                "This command may change files:\n\n" + shown + "\n\nRun it?"):
            self.console.state.set("not started")
            return False
        if self.store is not None:
            self.store.save(self.spec.name, self.values())
        return self.console.run(args, shown)
