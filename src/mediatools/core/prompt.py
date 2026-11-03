"""Terminal questions."""
from __future__ import annotations

from typing import Callable


class Prompt:
    """Interactive yes/no and text questions.

    With ``assume_yes=True`` every yes/no question is answered with yes
    (used by ``--yes`` options); ``input_func`` can be replaced in tests.

    Attributes:
        assume_yes: Answer every yes/no question with yes.
        input: Function used to read an answer.
    """

    YES = ("y", "yes", "j", "ja")
    NO = ("n", "no", "nein")

    def __init__(self, assume_yes: bool = False, input_func: Callable[[str], str] = input):
        """Configure the prompt.

        Args:
            assume_yes: Answer every yes/no question with yes.
            input_func: Function that shows a prompt and returns the answer.
        """
        self.assume_yes = assume_yes
        self.input = input_func

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
            try:
                text = self.input(question + suffix).strip().lower()
            except EOFError:
                text = ""
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
        try:
            answer = self.input(shown)
        except EOFError:
            return default
        return answer.strip() or default
