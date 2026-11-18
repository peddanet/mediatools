"""Dialog for a question of a running command (one button per answer)."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable


class ChoiceDialog:
    """Shows a question of the running command with one button per answer.

    The command announces each question as a line of JSON
    (:meth:`Prompt.announce`); the console view hides that line and opens
    this dialog. A click - or the key of the answer, or Enter for the
    default - sends the answer to the command exactly as if it had been
    typed into the input line, which still works as well. Closing the
    window sends nothing; the question stays open in the console.

    Attributes:
        window: The dialog window.
        send: Called with the chosen answer.
        buttons: Button per answer.
    """

    WRAP = 560

    def __init__(self, master: tk.Misc, spec: dict, send: Callable[[str], None]):
        """Build and show the dialog.

        Args:
            master: Parent widget.
            spec: ``{"question": str, "choices": [[answer, label], ...],
                "default": answer}``.
            send: Called with the chosen answer.
        """
        self.send = send
        self.window = tk.Toplevel(master)
        self.window.title("mediatools - question")
        self.window.transient(master.winfo_toplevel())
        frame = ttk.Frame(self.window, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=spec.get("question") or "Please choose",
                  wraplength=self.WRAP, justify="left").pack(anchor="w", pady=(0, 10))
        row = ttk.Frame(frame)
        row.pack(fill="x")
        default = spec.get("default", "")
        self.buttons: dict[str, ttk.Button] = {}
        for key, label in spec.get("choices", []):
            text = f"{label} [{key}]" if key else label
            button = ttk.Button(row, text=text, command=self.chooser(key))
            button.pack(side="left", padx=(0, 6), pady=2)
            self.buttons[key] = button
            if key:
                self.window.bind(f"<KeyPress-{key}>", self.on_key(key))
        if default in self.buttons:
            self.buttons[default].focus_set()
            self.window.bind("<Return>", self.on_key(default))
        self.window.protocol("WM_DELETE_WINDOW", self.close)

    def chooser(self, answer: str) -> Callable[[], None]:
        """Return the command of the button of an answer.

        Args:
            answer: The answer.

        Returns:
            Callable that sends it.
        """
        return lambda: self.choose(answer)

    def on_key(self, answer: str) -> Callable[[tk.Event], None]:
        """Return the key handler of an answer.

        Args:
            answer: The answer.

        Returns:
            Event handler that sends it.
        """
        return lambda _event: self.choose(answer)

    def choose(self, answer: str) -> None:
        """Send an answer and close the dialog.

        Args:
            answer: The chosen answer.
        """
        self.close()
        self.send(answer)

    def close(self) -> None:
        """Close the dialog without sending anything."""
        if self.window.winfo_exists():
            self.window.destroy()
