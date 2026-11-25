"""Read-only text area for the end of a log: scrolls both ways, follows the end on request."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class LogArea:
    """Shows the last lines of a log that is still being written.

    Long lines are not wrapped; a vertical and a horizontal scrollbar move
    the view. While the view shows the end it follows new lines; after the
    user scrolled up it stays at the line at its upper edge, and the
    horizontal position stays as well. Whether it follows is decided by the
    user's last scroll (:meth:`scrolled`), not by the view at refresh time:
    an area too small to show a line reported "not at the end" and stayed at
    the beginning for good (up to mediatools 1.15.3).

    Attributes:
        frame: Container of text and scrollbars (place it in the parent).
        text: The text widget.
        follow: True while the view follows new lines.
    """

    def __init__(self, master: tk.Misc, height: int = 4) -> None:
        """Build the area.

        Args:
            master: Parent widget.
            height: Height in lines.
        """
        self.frame = ttk.Frame(master)
        self.text = tk.Text(self.frame, height=height, font="TkFixedFont", wrap="none",
                            state="disabled")
        down = ttk.Scrollbar(self.frame, orient="vertical", command=self.scroll_y)
        across = ttk.Scrollbar(self.frame, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=down.set, xscrollcommand=across.set)
        self.text.grid(row=0, column=0, sticky="nsew")
        down.grid(row=0, column=1, sticky="ns")
        across.grid(row=1, column=0, sticky="ew")
        self.frame.rowconfigure(0, weight=1)
        self.frame.columnconfigure(0, weight=1)
        self.follow = True
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>", "<KeyRelease>"):
            self.text.bind(sequence, lambda _event: self.text.after_idle(self.scrolled),
                           add="+")

    def scroll_y(self, *args: str) -> None:
        """Vertical scrollbar: move the view, then note where the user left it.

        Args:
            *args: Arguments of the scrollbar for :meth:`tk.Text.yview`.
        """
        self.text.yview(*args)
        self.scrolled()

    def scrolled(self) -> None:
        """Remember after a scroll by the user whether the view shows the end."""
        self.follow = self.text.yview()[1] >= 0.999

    def show(self, content: str) -> None:
        """Replace the text if it changed, keeping the view as described above.

        Args:
            content: Last lines of the log.
        """
        if self.text.get("1.0", "end-1c") == content:
            return
        first = self.text.yview()[0]
        across = self.text.xview()[0]
        anchor = self.text.get("@0,0 linestart", "@0,0 lineend")
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", content)
        if self.follow:
            self.text.see("end-1c linestart")
        else:
            found = self.line_of(anchor)
            if found:
                self.text.yview(found)
            else:
                self.text.yview_moveto(first)
        self.text.xview_moveto(across)
        self.text.configure(state="disabled")

    def line_of(self, line: str) -> str:
        """Find a whole line.

        Args:
            line: Text of the line (empty: nothing to find).

        Returns:
            Index of the first line with exactly this text, or "".
        """
        start = "1.0"
        while line:
            found = self.text.search(line, start, stopindex="end", exact=True)
            if not found:
                return ""
            if (self.text.compare(found, "==", f"{found} linestart")
                    and self.text.get(found, f"{found} lineend") == line):
                return found
            start = f"{found} +1c"
        return ""
