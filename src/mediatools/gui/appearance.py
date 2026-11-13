"""Look of the GUI: font size, colour scheme and ttk styles."""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

from mediatools.core.config import Config
from mediatools.gui.systemlook import SystemLook


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
    (``auto``, ``light`` or ``dark``). :meth:`zoom` and :meth:`set_theme`
    apply a change at once and save it.

    ``auto`` (the default since 1.6.1) follows the desktop: the preferred
    scheme and accent colour of the settings portal (:class:`SystemLook`).
    :meth:`probe` reads them (in a thread, see :meth:`watch`) and
    :meth:`take_probe` applies a change in the Tk thread, so switching the
    desktop to dark switches the window within seconds. Without a portal or
    preference the scheme is :attr:`FALLBACK`.

    Attributes:
        root: Tk main window.
        config: Configuration file, or None (nothing is saved).
        scale: Font scale.
        theme: ``auto``, ``light`` or ``dark``.
        style: ttk style object.
        system: Reader of the desktop settings.
        detected: Scheme and accent colour of the desktop last applied.
        probed: Scheme and accent colour last read, not yet applied.
        choice: Tk variable of the setting (radio buttons of the menu).
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
    THEMES = ("auto", "light", "dark")
    FALLBACK = "dark"
    FONTS = ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont",
             "TkSmallCaptionFont", "TkIconFont", "TkTooltipFont", "TkFixedFont")

    def __init__(self, root: tk.Misc, config: Config | None = None,
                 system: SystemLook | None = None):
        """Read the settings and, for ``auto``, the desktop's choice.

        Args:
            root: Tk main window.
            config: Configuration file; None uses the defaults and saves nothing.
            system: Reader of the desktop settings; the portal if None.
        """
        self.root = root
        self.config = config
        self.scale = self.clamp(self._number("scale", 1.0))
        theme = config.get("gui", "theme") if config else ""
        self.theme = theme if theme in self.THEMES else "auto"
        self.style = ttk.Style(root)
        self.system = system or SystemLook()
        self.detected: tuple[str | None, str | None] = (None, None)
        self.probed: tuple[str | None, str | None] | None = None
        self.choice = tk.StringVar(master=root, value=self.theme)
        if self.theme == "auto":
            self.probe()
            self.detected = self.probed or (None, None)
            self.probed = None

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

    def scheme(self) -> str:
        """Return the scheme in effect.

        Returns:
            ``light`` or ``dark``.
        """
        if self.theme != "auto":
            return self.theme
        return self.detected[0] or self.FALLBACK

    @property
    def colors(self) -> dict[str, str]:
        """Colours of the scheme in effect, with the desktop's accent for ``auto``."""
        colors = dict(self.PALETTES[self.scheme()])
        accent = self.detected[1] if self.theme == "auto" else None
        if accent:
            colors["accent"] = accent
            colors["active"] = self.mix(accent, colors["fg"], 0.25)
            colors["accent_fg"] = "#0d1117" if self.luminance(accent) > 0.5 else "#ffffff"
        return colors

    @staticmethod
    def channel(text: str) -> float:
        """Linearise one sRGB channel.

        Args:
            text: Two hex digits.

        Returns:
            Linear value 0..1.
        """
        value = int(text, 16) / 255
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    @staticmethod
    def luminance(color: str) -> float:
        """Return the relative luminance of a colour (WCAG 2).

        Args:
            color: ``#rrggbb``.

        Returns:
            0 (black) to 1 (white).
        """
        red, green, blue = (Appearance.channel(color[i:i + 2]) for i in (1, 3, 5))
        return 0.2126 * red + 0.7152 * green + 0.0722 * blue

    @staticmethod
    def mix(first: str, second: str, share: float) -> str:
        """Mix two colours.

        Args:
            first: ``#rrggbb``.
            second: ``#rrggbb``.
            share: Part of ``second`` (0..1).

        Returns:
            The mixed colour ``#rrggbb``.
        """
        parts = [round(int(first[i:i + 2], 16) * (1 - share) + int(second[i:i + 2], 16) * share)
                 for i in (1, 3, 5)]
        return "#" + "".join(f"{part:02x}" for part in parts)

    def probe(self) -> None:
        """Read scheme and accent colour of the desktop (may run in a thread)."""
        self.probed = (self.system.scheme(), self.system.accent())

    def take_probe(self) -> bool:
        """Apply the last reading if it differs (Tk thread only).

        Returns:
            True if the look changed.
        """
        probed, self.probed = self.probed, None
        if probed is None or self.theme != "auto" or probed == self.detected:
            return False
        self.detected = probed
        self.apply()
        return True

    def watch(self, interval_ms: int = 3000) -> None:
        """Follow the desktop while ``auto`` is set (reads in a thread).

        Args:
            interval_ms: Time between two readings.
        """
        self.take_probe()
        if self.theme == "auto":
            threading.Thread(target=self.probe, daemon=True).start()
        self.root.after(interval_ms, lambda: self.watch(interval_ms))

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

    def set_theme(self, theme: str) -> str:
        """Choose ``auto``, ``light`` or ``dark`` and save it.

        Args:
            theme: The setting.

        Returns:
            The scheme in effect afterwards.

        Raises:
            ValueError: For another value.
        """
        if theme not in self.THEMES:
            raise ValueError(f"theme must be one of {self.THEMES}")
        self.theme = theme
        self.choice.set(theme)
        if theme == "auto":
            self.probe()
            self.take_probe()
        self.apply()
        self.save("theme", theme)
        return self.scheme()

    def save(self, key: str, value: str) -> None:
        """Write one value of ``[gui]``.

        Args:
            key: Key.
            value: Value.
        """
        if self.config is not None:
            self.config.set_value("gui", key, value)
