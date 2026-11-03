"""One player process per video and detection of closed windows."""
from __future__ import annotations

import os
import shlex
import subprocess
import time


class PlayerSession:
    """Starts a batch of player processes and classifies their state.

    Closing a player window ends its process with exit code 0. After the
    user confirms, :meth:`classify` sorts every file into

    * ``closed`` - process ended normally after the start window: discard
    * ``open`` - process still running: keep
    * ``errors`` - process ended during the start window or with an error
      code: keep (protects against single-instance handover and crashes)

    VLC is started with ``--no-one-instance --no-playlist-enqueue`` so that
    each file gets its own process even if "allow only one instance" is set
    in the VLC preferences.

    Attributes:
        argv: Player command prefix; the file path is appended.
        start_window: Seconds after start in which an exit counts as error.
        stagger: Pause between two player starts in seconds.
        items: One dict per started file: ``path``, ``proc``, ``early``.
    """

    def __init__(self, player: str = "vlc", audio: bool = False,
                 start_window: float = 3.0, stagger: float = 0.3):
        """Configure the session.

        Args:
            player: Player command, e.g. ``vlc`` or ``mpv``.
            audio: Play with sound.
            start_window: Seconds after start in which an exit is an error.
            stagger: Pause between two player starts in seconds.
        """
        self.argv = self.build_argv(player, audio)
        self.start_window = start_window
        self.stagger = stagger
        self.items: list[dict] = []

    @staticmethod
    def build_argv(player: str, audio: bool) -> list[str]:
        """Build the player command with safe defaults for VLC and mpv.

        Args:
            player: Player command; may contain arguments
                (e.g. ``flatpak run org.videolan.VLC``).
            audio: Play with sound.

        Returns:
            Argument list without the file path.
        """
        argv = shlex.split(player)
        is_vlc = any("vlc" in os.path.basename(token).lower() for token in argv)
        if is_vlc:
            argv += ["--no-one-instance", "--no-playlist-enqueue", "--loop"]
            if not audio:
                argv.append("--no-audio")
        elif os.path.basename(argv[0]) == "mpv":
            argv += ["--loop-file=inf", "--force-window=yes"]
            if not audio:
                argv.append("--mute=yes")
        return argv

    def open(self, paths: list[str]) -> None:
        """Start one process per file, then flag processes that ended at once.

        Args:
            paths: Files to show.
        """
        self.items = []
        for path in paths:
            # one independent process per file; output is discarded
            # pylint: disable-next=consider-using-with
            proc = subprocess.Popen(self.argv + [os.path.abspath(path)],
                                    stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
            self.items.append({"path": path, "proc": proc, "early": False})
            time.sleep(self.stagger)
        time.sleep(self.start_window)
        for item in self.items:
            if item["proc"].poll() is not None:
                item["early"] = True

    def classify(self) -> tuple[list[str], list[str], list[tuple[str, int]]]:
        """Sort the files by the state of their player process.

        Returns:
            ``(closed, still_open, errors)``; ``errors`` holds
            ``(path, exit_code)`` pairs.
        """
        closed, still_open, errors = [], [], []
        for item in self.items:
            code = item["proc"].poll()
            if code is None:
                still_open.append(item["path"])
            elif item["early"] or code != 0:
                errors.append((item["path"], code))
            else:
                closed.append(item["path"])
        return closed, still_open, errors

    def close_all(self, grace: float = 5.0) -> None:
        """Terminate remaining processes (SIGTERM, then SIGKILL).

        Args:
            grace: Seconds to wait for a clean exit before SIGKILL.
        """
        for item in self.items:
            if item["proc"].poll() is None:
                item["proc"].terminate()
        deadline = time.monotonic() + grace
        for item in self.items:
            if item["proc"].poll() is None:
                try:
                    item["proc"].wait(timeout=max(0.1, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    item["proc"].kill()
