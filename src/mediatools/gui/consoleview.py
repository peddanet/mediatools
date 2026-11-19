"""Output pane of a command: live output, answers to questions, cancel."""
from __future__ import annotations

import json
import re
import tkinter as tk
from tkinter import scrolledtext, ttk
from typing import Callable

from mediatools.core.prompt import Prompt
from mediatools.gui.choicedialog import ChoiceDialog
from mediatools.gui.jobrunner import JobRunner


class ConsoleView:
    """Shows the output of one command as it arrives and lets the user answer.

    Questions of the command (``[y/N]``, a choice of versions or disks)
    are answered in the input line; Enter sends the answer. A question the
    command announces for the GUI (:meth:`Prompt.announce`) also opens a
    dialog with one button per answer (:class:`ChoiceDialog`); the
    announcement line itself is not shown. *Cancel* acts like Ctrl+C in a
    terminal, a second click terminates the command.

    Attributes:
        frame: Container of the widgets (place it in the parent).
        runner: Runs the command.
        text: Output area (read-only).
        answer: Input line for answers.
        state: Text of the status line.
        on_finish: Called with the exit code when a command ends, or None.
        poll_ms: Interval of output checks in milliseconds.
        dialog: Open question dialog, or None.
        held: Start of an announcement line whose end has not arrived yet.
        tail: End of the output of the current command (for :meth:`finished`).
        ask: Opens the dialog for an announced question (replaceable in tests).
    """

    BACKGROUND = re.compile(r"(\w+) continues in the background \(pid (\d+)\)")

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
        self.dialog: ChoiceDialog | None = None
        self.held = ""
        self.tail = ""
        self.ask: Callable[[dict], None] = self.open_dialog

    def take_questions(self, text: str) -> str:
        """Remove the announcement lines of questions and open their dialogs.

        Args:
            text: New output (line ends already normalised).

        Returns:
            The text to show.
        """
        text, self.held = self.held + text, ""
        shown = []
        for piece in text.splitlines(keepends=True):
            if piece.startswith(Prompt.MARKER) or (Prompt.MARKER.startswith(piece)
                                                   and not piece.endswith("\n")):
                if not piece.endswith("\n"):
                    self.held = piece        # rest of the line follows later
                    continue
                try:
                    spec = json.loads(piece[len(Prompt.MARKER):])
                except ValueError:
                    shown.append(piece)
                    continue
                self.ask(spec)
            else:
                shown.append(piece)
        return "".join(shown)

    def open_dialog(self, spec: dict) -> None:
        """Show the dialog of an announced question.

        Args:
            spec: Announcement (question, choices, default).
        """
        self.close_dialog()
        self.dialog = ChoiceDialog(self.frame, spec, self.answer_choice)

    def close_dialog(self) -> None:
        """Close an open question dialog."""
        if self.dialog is not None:
            self.dialog.close()
            self.dialog = None

    def answer_choice(self, answer: str) -> None:
        """Send the answer chosen in a dialog.

        Args:
            answer: The answer.
        """
        self.dialog = None
        try:
            self.runner.send(answer)
        except RuntimeError as exc:
            self.state.set(str(exc))
            return
        self.write(answer + "\n")

    def write(self, text: str) -> None:
        """Append text to the output area.

        The view follows the new text only while it shows the end; after
        scrolling up it stays where the user put it.

        Args:
            text: Text to append.
        """
        if not text:
            return
        text = self.take_questions(text.replace("\r\n", "\n").replace("\r", "\n"))
        if not text:
            return
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
        self.tail = ""
        self.runner.start(args)
        self.state.set("running: " + shown)
        self.answer.focus_set()
        self.frame.after(self.poll_ms, self.poll)
        return True

    def poll(self) -> None:
        """Show new output; report the exit code at the end."""
        text, code = self.runner.poll()
        self.write(text)
        self.tail = (self.tail + text)[-4000:]
        if code is None:
            self.frame.after(self.poll_ms, self.poll)
            return
        self.close_dialog()
        self.held = ""
        self.write(f"[exit code {code}]\n")
        self.state.set(self.finished(code))
        if self.on_finish is not None:
            self.on_finish(code)

    def finished(self, code: int) -> str:
        """Describe the end of a command for the status line.

        A command that handed its work to the background (``shrink`` and
        the like) has ended, but its job runs on; that is said explicitly.

        Args:
            code: Exit code.

        Returns:
            Text for the status line.
        """
        match = self.BACKGROUND.search(self.tail)
        if code == 0 and match:
            return (f"{match.group(1)} runs in the background (pid {match.group(2)}) - "
                    "this window may be closed; the shrink tab shows its progress")
        return "finished, exit code " + str(code)

    def send(self) -> None:
        """Send the input line to the running command."""
        answer = self.answer.get()
        self.close_dialog()
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
