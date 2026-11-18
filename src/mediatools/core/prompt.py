"""Terminal questions."""
from __future__ import annotations

import json
import os
from typing import Callable


class Prompt:
    """Interactive yes/no and text questions.

    With ``assume_yes=True`` every yes/no question is answered with yes
    (used by ``--yes`` options); ``input_func`` can be replaced in tests.

    **GUI.** When the GUI runs the command it sets the environment variable
    :attr:`GUI_ENV`. Before every question a single line starting with
    :attr:`MARKER` and followed by JSON (question, choices, default) is then
    printed; the GUI hides that line and shows the question as a dialog
    with one button per choice, whose click sends the answer as if typed.

    Attributes:
        assume_yes: Answer every yes/no question with yes.
        input: Function used to read an answer.
        ended: True once the input has ended (no further answers).
    """

    YES = ("y", "yes", "j", "ja")
    NO = ("n", "no", "nein")
    GUI_ENV = "MEDIATOOLS_GUI"
    MARKER = "@@mediatools-choice "

    def __init__(self, assume_yes: bool = False, input_func: Callable[[str], str] = input):
        """Configure the prompt.

        Args:
            assume_yes: Answer every yes/no question with yes.
            input_func: Function that shows a prompt and returns the answer.
        """
        self.assume_yes = assume_yes
        self.input = input_func
        self.ended = False

    @classmethod
    def announce(cls, question: str, choices: list[tuple[str, str]], default: str) -> None:
        """Print the machine-readable form of a question for the GUI.

        Args:
            question: Question text.
            choices: ``(answer, label)`` pairs.
            default: Answer of the default button.
        """
        if os.environ.get(cls.GUI_ENV) == "1":
            print(cls.MARKER + json.dumps({"question": question, "choices": choices,
                                           "default": default}), flush=True)

    def _read(self, shown: str) -> str | None:
        """Read one answer.

        Args:
            shown: Prompt text.

        Returns:
            The answer, or None at the end of input (also sets :attr:`ended`).
        """
        try:
            return self.input(shown)
        except EOFError:
            self.ended = True
            return None

    def choose(self, question: str, choices: list[tuple[str, str]], default: str,
               end: str) -> str:
        """Ask for one of several one-letter answers until a valid one is given.

        Args:
            question: Question text (may be empty).
            choices: ``(answer, label)`` pairs, e.g. ``("p", "play")``.
            default: Answer for empty input.
            end: Answer at the end of input (e.g. quit), so a closed input
                never loops.

        Returns:
            One of the answers of ``choices``.
        """
        keys = [key for key, _label in choices]
        menu = "  ".join(f"[{key}] {label}" for key, label in choices)
        while True:
            self.announce(question, choices, default)
            text = self._read(f"{question}\n{menu} [{default}]: " if question
                              else f"{menu} [{default}]: ")
            if text is None:
                return end
            answer = text.strip().lower()[:1] or default
            if answer in keys:
                return answer
            print("Please answer " + ", ".join(keys[:-1]) + " or " + keys[-1] + ".")

    def pause(self, message: str) -> None:
        """Wait until the user confirms (Enter, or the GUI button).

        Args:
            message: What the user confirms.
        """
        self.announce(message, [("", "continue")], "")
        self._read(message + ": ")

    def yes_no(self, question: str, default: bool = False) -> bool:
        """Ask a yes/no question until a valid answer is given.

        Args:
            question: Question text without the ``[y/N]`` suffix.
            default: Result for empty input or end of input.

        Returns:
            True for yes, False for no.
        """
        if self.assume_yes:
            print(question + " [auto: yes]")
            return True
        suffix = " [Y/n]: " if default else " [y/N]: "
        answer = None
        while answer is None:
            self.announce(question, [("y", "yes"), ("n", "no")], "y" if default else "n")
            read = self._read(question + suffix)
            text = "" if read is None else read.strip().lower()
            if not text:
                answer = default
            elif text in self.YES:
                answer = True
            elif text in self.NO:
                answer = False
            else:
                print("Please answer y or n.")
        return answer

    def text(self, question: str, default: str = "") -> str:
        """Ask for free text.

        Args:
            question: Question text.
            default: Result for empty input or end of input.

        Returns:
            The stripped answer or ``default``.
        """
        shown = f"{question} [{default}]: " if default else f"{question}: "
        answer = self._read(shown)
        if answer is None:
            return default
        return answer.strip() or default
