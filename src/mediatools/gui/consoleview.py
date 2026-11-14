"""Output pane of a command: live output, answers to questions, cancel."""
from __future__ import annotations

import tkinter as tk
from tkinter import scrolledtext, ttk
from typing import Callable

from mediatools.gui.jobrunner import JobRunner


class ConsoleView:
    """Shows the output of one command as it arrives and lets the user answer.

    Questions of the command (``[y/N]``, a choice of versions or disks)
    are answered in the input line; Enter sends the answer. *Cancel* acts
    like Ctrl+C in a terminal, a second click terminates the command.

    Attributes:
        frame: Container of the widgets (place it in the parent).
        runner: Runs the command.
        text: Output area (read-only).
        answer: Input line for answers.
        state: Text of the status line.
        on_finish: Called with the exit code when a command ends, or None.
        poll_ms: Interval of output checks in milliseconds.
    """

    def __init__(self, master: tk.Misc, runner: JobRunner | None = None,
                 on_finish: Callable[[int], None] | None = None, poll_ms: int = 100,
                 height: int = 12):
        """Build the pane.

        Args:
            master: Parent widget.
            runner: Runner to use; a new one if None.
            on_finish: Called with the exit code when a command ends.
            poll_ms: Interval of output checks in milliseconds.
            height: Height of the output area in lines.
        """
        self.frame = ttk.Frame(master)
        self.runner = runner or JobRunner()
        self.on_finish = on_finish
        self.poll_ms = poll_ms
        self.text = scrolledtext.ScrolledText(self.frame, height=height, wrap="char",
                                              font="TkFixedFont", state="disabled")
        self.text.pack(fill="both", expand=True)
        toolbar = ttk.Frame(self.frame)
        toolbar.pack(fill="x", pady=(4, 0))
        ttk.Label(toolbar, text="Answer:").pack(side="left")
        self.answer = ttk.Entry(toolbar)
        self.answer.pack(side="left", fill="x", expand=True, padx=4)
        self.answer.bind("<Return>", lambda _event: self.send())
        ttk.Button(toolbar, text="Send", command=self.send).pack(side="left")
        ttk.Button(toolbar, text="Cancel", command=self.cancel).pack(side="left", padx=4)
        ttk.Button(toolbar, text="Clear", command=self.clear).pack(side="left")
        self.state = tk.StringVar(value="ready")
        ttk.Label(self.frame, textvariable=self.state).pack(fill="x")

    def write(self, text: str) -> None:
        """Append text to the output area.

        The view follows the new text only while it shows the end; after
        scrolling up it stays where the user put it.

        Args:
            text: Text to append.
        """
        if not text:
            return
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        at_end = self.text.yview()[1] >= 0.999
        self.text.configure(state="normal")
        self.text.insert("end", text)
        if at_end:
            self.text.see("end")
        self.text.configure(state="disabled")

    def clear(self) -> None:
        """Empty the output area."""
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    def run(self, args: list[str], shown: str) -> bool:
        """Start a command and follow its output.

        Args:
            args: Words after the program name.
            shown: Command line shown above the output.

        Returns:
            False if a command of this pane still runs.
        """
        if self.runner.running:
            self.state.set("a command is still running - cancel it or wait")
            return False
        self.write(f"\n$ {shown}\n")
        self.runner.start(args)
        self.state.set("running: " + shown)
        self.answer.focus_set()
        self.frame.after(self.poll_ms, self.poll)
        return True

    def poll(self) -> None:
        """Show new output; report the exit code at the end."""
        text, code = self.runner.poll()
        self.write(text)
        if code is None:
            self.frame.after(self.poll_ms, self.poll)
            return
        self.write(f"[exit code {code}]\n")
        self.state.set("finished, exit code " + str(code))
        if self.on_finish is not None:
            self.on_finish(code)

    def send(self) -> None:
        """Send the input line to the running command."""
        answer = self.answer.get()
        try:
            self.runner.send(answer)
        except RuntimeError as exc:
            self.state.set(str(exc))
            return
        self.write(answer + "\n")
        self.answer.delete(0, "end")

    def cancel(self) -> None:
        """Interrupt the running command."""
        self.state.set(self.runner.cancel())
