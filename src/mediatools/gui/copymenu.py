"""Copy text from any part of the window: labels, lists and text areas."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class CopyMenu:
    """Right-click menu "Copy" / "Copy all" for every label, list and text area.

    Installed once for the whole window (class bindings), so every widget of
    these classes gets it, also ones created later:

    * text areas (log, output, results): "Copy" takes the marked text,
      "Copy all" the whole content; marking works with the mouse even where
      the area is read-only, and Ctrl+C copies the marking;
    * lists (queue, jobs, files): "Copy" takes the selected rows, "Copy all"
      every row, one line per row with the columns separated by tabs;
      Ctrl+C copies the selected rows;
    * labels (status lines): both copy the label's text.

    The text goes to the clipboard of the desktop, so it can be pasted into
    any other program while mediatools runs.

    Attributes:
        root: The main window.
        menu: The context menu.
        target: Widget the menu was opened on.
    """

    CLASSES = ("Text", "Treeview", "TLabel", "Label")

    def __init__(self, root: tk.Tk) -> None:
        """Bind the menu and Ctrl+C for all widgets of :attr:`CLASSES`.

        Args:
            root: The main window.
        """
        self.root = root
        self.menu = tk.Menu(root, tearoff=False)
        self.menu.add_command(label="Copy", command=lambda: self.copy(all_of_it=False))
        self.menu.add_command(label="Copy all", command=lambda: self.copy(all_of_it=True))
        self.target: tk.Misc | None = None
        for name in self.CLASSES:
            root.bind_class(name, "<Button-3>", self.open, add="+")
        root.bind_class("Text", "<Button-1>", lambda event: event.widget.focus_set(), add="+")
        root.bind_class("Treeview", "<Control-c>", self.copy_key, add="+")
        root.bind_class("Text", "<Control-c>", self.copy_key, add="+")

    def open(self, event: tk.Event) -> None:
        """Show the menu at the mouse on the clicked widget.

        Args:
            event: The right click.
        """
        self.target = event.widget
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def copy_key(self, event: tk.Event) -> str:
        """Ctrl+C: copy the marking (text area) or the selected rows (list).

        Args:
            event: The key event.

        Returns:
            "break", so the class binding does not copy a second time.
        """
        self.target = event.widget
        self.copy(all_of_it=False)
        return "break"

    def copy(self, all_of_it: bool) -> str:
        """Put the text of :attr:`target` on the clipboard.

        Args:
            all_of_it: True for everything, False for the marking or the
                selected rows (everything if nothing is marked).

        Returns:
            The copied text ("" if there was nothing).
        """
        text = self.text_of(self.target, all_of_it) if self.target is not None else ""
        if text:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
        return text

    @staticmethod
    def text_of(widget: tk.Misc, all_of_it: bool) -> str:
        """Return the text a widget offers for copying.

        Args:
            widget: Text area, list or label.
            all_of_it: True for everything, False for the marking or the
                selected rows (everything if nothing is marked).

        Returns:
            The text, "" for other widgets.
        """
        if isinstance(widget, tk.Text):
            if not all_of_it and widget.tag_ranges("sel"):
                return widget.get("sel.first", "sel.last")
            return widget.get("1.0", "end-1c")
        if isinstance(widget, ttk.Treeview):
            rows = list(widget.selection()) if not all_of_it else []
            rows = rows or list(widget.get_children())
            return "\n".join("\t".join(str(part) for part in
                                       ([widget.item(row, "text")] if widget.item(row, "text")
                                        else []) + list(widget.item(row, "values")))
                             for row in rows)
        if isinstance(widget, (ttk.Label, tk.Label)):
            variable = str(widget.cget("textvariable"))
            return str(widget.getvar(variable) if variable else widget.cget("text"))
        return ""
