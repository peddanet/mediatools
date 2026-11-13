"""Look of the GUI: font size, colour scheme and ttk styles."""
from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

from mediatools.core.config import Config


class Appearance:
    """Applies a flat, calm style with a selectable size and colour scheme.

    Tk's default look on Linux dates from the 1990s: grey bevelled
    buttons and small fonts. This class builds a style on the ``clam``
    theme, which ships with every Tk and can be coloured completely: flat
    surfaces, one accent colour (selected tab, progress bars, the *Run*
    button), more padding and fonts scaled by :attr:`scale`. Classic Tk
    widgets (text areas, menus) are coloured as well. No extra package is
    needed.

    The settings live in ``[gui]`` of ``mediatools.ini``: ``scale``
    (1.0 = 10 pt; :attr:`MIN_SCALE` to :attr:`MAX_SCALE`) and ``theme``
    (``light`` or ``dark``). :meth:`zoom` and :meth:`toggle_theme` apply a
    change at once and save it.

    Attributes:
        root: Tk main window.
        config: Configuration file, or None (nothing is saved).
        scale: Font scale.
        theme: ``light`` or ``dark``.
        style: ttk style object.
    """

    BASE_POINTS = 10
    MIN_SCALE = 0.8
    MAX_SCALE = 2.5
    STEP = 0.1
    PALETTES = {
        "light": {"bg": "#f4f5f7", "surface": "#ffffff", "fg": "#1f2933", "muted": "#5f6b7a",
                  "accent": "#2f6fde", "accent_fg": "#ffffff", "active": "#2559b5",
                  "border": "#d3d8df", "select": "#dbe7fb", "hover": "#eaedf1"},
        "dark": {"bg": "#1d2126", "surface": "#262b31", "fg": "#e6e9ed", "muted": "#9aa4b1",
                 "accent": "#4c8dff", "accent_fg": "#0d1117", "active": "#79a8ff",
                 "border": "#3a414a", "select": "#2d4a7a", "hover": "#30363e"},
    }
    FONTS = ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont",
             "TkSmallCaptionFont", "TkIconFont", "TkTooltipFont", "TkFixedFont")

    def __init__(self, root: tk.Misc, config: Config | None = None):
        """Read the settings.

        Args:
            root: Tk main window.
            config: Configuration file; None uses the defaults and saves nothing.
        """
        self.root = root
        self.config = config
        self.scale = self.clamp(self._number("scale", 1.0))
        theme = config.get("gui", "theme") if config else ""
        self.theme = theme if theme in self.PALETTES else "light"
        self.style = ttk.Style(root)

    def _number(self, key: str, fallback: float) -> float:
        """Read a number of ``[gui]``.

        Args:
            key: Key in ``[gui]``.
            fallback: Value if unset or not a number.

        Returns:
            The number.
        """
        try:
            return float(self.config.get("gui", key)) if self.config else fallback
        except ValueError:
            return fallback

    @classmethod
    def clamp(cls, scale: float) -> float:
        """Limit a scale to the allowed range.

        Args:
            scale: Requested scale.

        Returns:
            Scale between :attr:`MIN_SCALE` and :attr:`MAX_SCALE`, one decimal.
        """
        return round(min(cls.MAX_SCALE, max(cls.MIN_SCALE, scale)), 1)

    @property
    def colors(self) -> dict[str, str]:
        """Colours of the current scheme."""
        return self.PALETTES[self.theme]

    def points(self) -> int:
        """Return the base font size.

        Returns:
            Points for the current scale.
        """
        return max(6, int(round(self.BASE_POINTS * self.scale)))

    def apply(self) -> None:
        """Apply fonts, ttk styles and the colours of classic widgets."""
        size = self.points()
        for name in self.FONTS:
            try:
                named = tkfont.nametofont(name, root=self.root)
            except tk.TclError:
                continue
            named.configure(size=size, weight="bold" if name == "TkHeadingFont" else "normal")
        self._styles(size)
        c = self.colors
        for key, value in (("background", c["surface"]), ("foreground", c["fg"]),
                           ("selectBackground", c["select"]),
                           ("selectForeground", c["fg"])):
            self.root.option_add("*TCombobox*Listbox." + key, value)
        self._classic(self.root)

    def _styles(self, size: int) -> None:
        """Configure the ttk styles.

        Args:
            size: Base font size in points.
        """
        c = self.colors
        pad = max(2, int(4 * self.scale))
        style = self.style
        style.theme_use("clam")
        style.configure(".", background=c["bg"], foreground=c["fg"], bordercolor=c["border"],
                        lightcolor=c["bg"], darkcolor=c["bg"], troughcolor=c["border"],
                        fieldbackground=c["surface"], selectbackground=c["select"],
                        selectforeground=c["fg"], insertcolor=c["fg"], focuscolor=c["accent"],
                        font="TkDefaultFont")
        style.configure("TFrame", background=c["bg"])
        style.configure("TLabel", background=c["bg"], foreground=c["fg"])
        style.configure("Hint.TLabel", foreground=c["muted"])
        style.configure("Title.TLabel", font="TkHeadingFont")
        style.configure("TButton", background=c["surface"], padding=(3 * pad, pad),
                        relief="flat", borderwidth=1)
        style.map("TButton", background=[("pressed", c["select"]), ("active", c["hover"])])
        style.configure("Accent.TButton", background=c["accent"], foreground=c["accent_fg"],
                        bordercolor=c["accent"])
        style.map("Accent.TButton", background=[("pressed", c["active"]),
                                                ("active", c["active"])])
        style.configure("TNotebook", background=c["bg"], borderwidth=0, tabmargins=(4, 4, 4, 0))
        style.configure("TNotebook.Tab", background=c["bg"], foreground=c["muted"],
                        padding=(4 * pad, pad + 1), borderwidth=0)
        style.map("TNotebook.Tab", background=[("selected", c["surface"])],
                  foreground=[("selected", c["accent"])])
        style.configure("TEntry", fieldbackground=c["surface"], foreground=c["fg"],
                        padding=pad // 2)
        style.configure("TCombobox", fieldbackground=c["surface"], foreground=c["fg"],
                        arrowcolor=c["fg"], background=c["surface"])
        style.map("TCombobox", fieldbackground=[("readonly", c["surface"])],
                  foreground=[("readonly", c["fg"])])
        style.configure("TCheckbutton", background=c["bg"], indicatorbackground=c["surface"],
                        indicatorforeground=c["accent"], indicatorsize=int(size * 1.4),
                        indicatormargin=(0, 0, pad, 0))
        style.configure("Horizontal.TProgressbar", background=c["accent"],
                        troughcolor=c["border"], bordercolor=c["border"],
                        lightcolor=c["accent"], darkcolor=c["accent"],
                        thickness=max(8, int(12 * self.scale)))
        style.configure("Treeview", background=c["surface"], fieldbackground=c["surface"],
                        foreground=c["fg"], rowheight=int(size * 2.4), borderwidth=0)
        style.map("Treeview", background=[("selected", c["select"])],
                  foreground=[("selected", c["fg"])])
        style.configure("Treeview.Heading", background=c["bg"], foreground=c["muted"],
                        relief="flat", font="TkHeadingFont")
        style.configure("TLabelframe", background=c["bg"], bordercolor=c["border"])
        style.configure("TLabelframe.Label", background=c["bg"], foreground=c["muted"])
        style.configure("TPanedwindow", background=c["bg"])
        style.configure("Sash", sashthickness=max(4, int(6 * self.scale)))

    def _classic(self, widget: tk.Misc) -> None:
        """Colour classic Tk widgets below a widget (text areas, menus, root).

        Args:
            widget: Start of the walk.
        """
        c = self.colors
        if isinstance(widget, (tk.Tk, tk.Toplevel)):
            widget.configure(background=c["bg"])
        elif isinstance(widget, tk.Text):
            widget.configure(background=c["surface"], foreground=c["fg"],
                             insertbackground=c["fg"], selectbackground=c["select"],
                             selectforeground=c["fg"], highlightthickness=0, borderwidth=0)
        elif isinstance(widget, tk.Menu):
            widget.configure(background=c["surface"], foreground=c["fg"],
                             activebackground=c["select"], activeforeground=c["fg"],
                             borderwidth=0)
        for child in widget.winfo_children():
            self._classic(child)
        if isinstance(widget, (tk.Tk, tk.Toplevel)):
            menu = widget.cget("menu")
            if menu:
                self._classic(widget.nametowidget(menu))

    def zoom(self, steps: int) -> float:
        """Change the font scale and save it.

        Args:
            steps: +1 larger, -1 smaller, 0 back to 1.0.

        Returns:
            The new scale.
        """
        self.scale = 1.0 if steps == 0 else self.clamp(self.scale + steps * self.STEP)
        self.apply()
        self.save("scale", f"{self.scale:.1f}")
        return self.scale

    def toggle_theme(self) -> str:
        """Switch between light and dark and save it.

        Returns:
            The new scheme.
        """
        self.theme = "dark" if self.theme == "light" else "light"
        self.apply()
        self.save("theme", self.theme)
        return self.theme

    def save(self, key: str, value: str) -> None:
        """Write one value of ``[gui]``.

        Args:
            key: Key.
            value: Value.
        """
        if self.config is not None:
            self.config.set_value("gui", key, value)
