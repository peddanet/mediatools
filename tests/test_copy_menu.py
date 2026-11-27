"""Copy text from labels, lists and text areas of the window (gui/copymenu.py)."""
import tkinter as tk
from tkinter import ttk

from test_gui_tk import fixture_root  # noqa: F401  pylint: disable=unused-import
from mediatools.gui.copymenu import CopyMenu


def test_text_area_copies_marking_or_everything(root: tk.Tk) -> None:
    """A read-only text area: the marking, else everything; Ctrl+C copies the marking.

    Args:
        root: Main window (fixture).
    """
    root.deiconify()                                  # key events need a shown window
    copier = CopyMenu(root)
    area = tk.Text(root)
    area.insert("1.0", "first line\nsecond line")
    area.configure(state="disabled")
    area.pack()
    root.update()
    copier.target = area
    assert copier.copy(all_of_it=False) == "first line\nsecond line"     # nothing marked
    area.tag_add("sel", "2.0", "2.6")
    assert copier.copy(all_of_it=False) == "second" == root.clipboard_get()
    assert copier.copy(all_of_it=True) == "first line\nsecond line"
    area.focus_force()
    root.update()
    area.event_generate("<Control-c>")
    assert root.clipboard_get() == "second"


def test_list_copies_selected_or_all_rows(root: tk.Tk) -> None:
    """A list: selected rows, else all; columns separated by tabs.

    Args:
        root: Main window (fixture).
    """
    copier = CopyMenu(root)
    rows = ttk.Treeview(root, columns=("a", "b"), show="headings")
    first = rows.insert("", "end", values=("running", "10 GiB"))
    rows.insert("", "end", values=("2", "5 GiB"))
    copier.target = rows
    assert copier.copy(all_of_it=False) == "running\t10 GiB\n2\t5 GiB"
    rows.selection_set(first)
    assert copier.copy(all_of_it=False) == "running\t10 GiB"
    assert copier.copy(all_of_it=True).count("\n") == 1


def test_label_copies_its_text_and_others_nothing(root: tk.Tk) -> None:
    """A label with a text variable or plain text; other widgets offer nothing.

    Args:
        root: Main window (fixture).
    """
    copier = CopyMenu(root)
    shown = tk.StringVar(root, value="job  99.4 %  ETA 1h42m")
    copier.target = ttk.Label(root, textvariable=shown)
    assert copier.copy(all_of_it=True) == "job  99.4 %  ETA 1h42m"
    copier.target = tk.Label(root, text="plain")
    assert copier.copy(all_of_it=False) == "plain"
    assert CopyMenu.text_of(ttk.Button(root, text="Stop"), all_of_it=True) == ""
    copier.target = ttk.Frame(root)
    root.clipboard_clear()
    root.clipboard_append("kept")
    assert copier.copy(all_of_it=True) == "" and root.clipboard_get() == "kept"


def test_right_click_opens_the_menu(root: tk.Tk) -> None:
    """The menu belongs to every label; it opens on the clicked widget.

    Args:
        root: Main window (fixture).
    """
    copier = CopyMenu(root)
    label = ttk.Label(root, text="status")
    label.pack()
    root.update()
    shown = []
    copier.menu.tk_popup = lambda x, y: shown.append((x, y))  # type: ignore[method-assign]
    label.event_generate("<Button-3>", x=2, y=2)
    assert copier.target is label and shown
