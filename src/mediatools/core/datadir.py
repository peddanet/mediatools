"""Location of configuration, runtime state and kept data."""
from __future__ import annotations

import os
from datetime import datetime


class DataDir:
    """Directories of mediatools, following the XDG Base Directory layout.

    Three places, each with a folder named after the application::

        ~/.config/mediatools/        configuration: mediatools.ini,
                                     privacy/denylist.txt, privacy/allowlist.txt
        ~/.local/state/mediatools/   runtime state: logs/, state/, reports/,
                                     snapshot/
        ~/.local/share/mediatools/   kept data: plans/ (flatten undo),
                                     releases/ (applied release archives)

    ``$XDG_CONFIG_HOME``, ``$XDG_STATE_HOME`` and ``$XDG_DATA_HOME`` replace
    the defaults when they hold an absolute path. The installed release
    (``venv``) is not data; it stays below ``~/scripts/mediatools.d``.

    Two cases keep everything in one folder with the old file names:

    * ``$MEDIATOOLS_HOME`` is set (tests, several independent setups);
    * the old folder ``~/scripts/mediatools.d`` still holds data (releases
      before 1.5.0) - ``mediatools where --migrate`` moves it.

    Nothing is written into the media collection itself except what a
    subcommand documents (shrink databases, ``_flat`` directories).

    Attributes:
        mode: ``xdg``, ``single`` (``$MEDIATOOLS_HOME``) or ``legacy``.
        config_dir: Folder of the configuration.
        state_dir: Folder of logs and runtime state.
        data_dir: Folder of kept data.
        root: Main folder: the state folder (``xdg``) or the single folder.
    """

    ENV = "MEDIATOOLS_HOME"
    LEGACY = "~/scripts/mediatools.d"
    APP = "mediatools"
    CONFIG_FILE = "mediatools.ini"
    DATA_SUBS = ("plans", "releases")
    MOVED_NOTE = "WHERE-IS-THE-DATA.txt"
    LEGACY_MARKS = ("logs", "state", "reports", "plans", CONFIG_FILE,
                    "privacy_denylist.txt", "privacy_allowlist.txt")
    XDG = {"config": ("XDG_CONFIG_HOME", "~/.config"),
           "state": ("XDG_STATE_HOME", "~/.local/state"),
           "data": ("XDG_DATA_HOME", "~/.local/share")}

    def __init__(self, root: str | None = None):
        """Resolve the folders.

        Args:
            root: Explicit single folder; if None, ``$MEDIATOOLS_HOME``, the
                old folder (if it still holds data) or the XDG layout is used.
        """
        single = root or os.environ.get(self.ENV)
        if single:
            self.mode = "single"
        elif self.legacy_in_use():
            self.mode, single = "legacy", self.LEGACY
        else:
            self.mode = "xdg"
        if single:
            folder = os.path.abspath(os.path.expanduser(single))
            self.config_dir = self.state_dir = self.data_dir = folder
        else:
            self.config_dir = self.xdg_dir("config")
            self.state_dir = self.xdg_dir("state")
            self.data_dir = self.xdg_dir("data")
        self.root = self.state_dir

    @classmethod
    def legacy_dir(cls) -> str:
        """Return the absolute path of the old single folder.

        Returns:
            Expanded ``~/scripts/mediatools.d``.
        """
        return os.path.abspath(os.path.expanduser(cls.LEGACY))

    @classmethod
    def legacy_in_use(cls) -> bool:
        """Tell whether the old single folder still holds data.

        Returns:
            True if logs, state, reports, plans, the INI file or a privacy
            list are there - unless the note of a finished move is there
            (an older tool may recreate a folder afterwards).
        """
        folder = cls.legacy_dir()
        if os.path.exists(os.path.join(folder, cls.MOVED_NOTE)):
            return False
        return any(os.path.exists(os.path.join(folder, mark)) for mark in cls.LEGACY_MARKS)

    @classmethod
    def xdg_dir(cls, kind: str) -> str:
        """Return the application folder of one XDG base directory.

        Args:
            kind: ``config``, ``state`` or ``data``.

        Returns:
            ``<base>/mediatools``; the variable counts only if absolute.
        """
        variable, default = cls.XDG[kind]
        base = os.environ.get(variable, "")
        if not os.path.isabs(base):
            base = os.path.expanduser(default)
        return os.path.join(base, cls.APP)

    @property
    def config_file(self) -> str:
        """Path of the INI file.

        Returns:
            ``<config_dir>/mediatools.ini``.
        """
        return os.path.join(self.config_dir, self.CONFIG_FILE)

    def privacy_file(self, kind: str) -> str:
        """Path of a local list of the privacy scan.

        Args:
            kind: ``deny`` or ``allow``.

        Returns:
            ``<config_dir>/privacy/<kind>list.txt``, or the old name
            ``privacy_<kind>list.txt`` in the single folder.
        """
        if self.mode == "xdg":
            return os.path.join(self.config_dir, "privacy", f"{kind}list.txt")
        return os.path.join(self.config_dir, f"privacy_{kind}list.txt")

    def sub(self, name: str) -> str:
        """Return a subfolder, creating it if needed.

        ``plans`` and ``releases`` belong to the data folder, all others
        to the state folder.

        Args:
            name: Subfolder name, e.g. ``logs``.

        Returns:
            Absolute path of the subfolder.
        """
        base = self.data_dir if name in self.DATA_SUBS else self.state_dir
        path = os.path.join(base, name)
        os.makedirs(path, exist_ok=True)
        return path

    def file(self, sub: str, prefix: str, suffix: str) -> str:
        """Build a time-stamped file path inside a subfolder.

        Args:
            sub: Subfolder name, e.g. ``logs``.
            prefix: File name prefix, e.g. ``shrink``.
            suffix: File name suffix including the dot, e.g. ``.log``.

        Returns:
            Path such as ``<state>/logs/shrink_2026-11-03_13-12-52.log``.
        """
        return os.path.join(self.sub(sub), f"{prefix}_{self.stamp()}{suffix}")

    @staticmethod
    def stamp() -> str:
        """Return the current time in the format used in file names.

        Returns:
            Timestamp ``YYYY-MM-DD_hh-mm-ss``.
        """
        return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
