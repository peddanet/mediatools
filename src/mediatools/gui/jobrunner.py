"""Runs one mediatools command for the GUI and collects its output."""
from __future__ import annotations

import codecs
import os
import queue
import signal
import subprocess
import sys
import threading


class JobRunner:
    """Starts a command as a child process and streams its output.

    The GUI runs every command as ``python -m mediatools ...`` with the
    same interpreter, so it uses the same release as the GUI itself and
    behaves exactly like the command line. Output (standard output and
    error together) arrives in pieces through :meth:`poll`, also text
    without a line end, so that questions such as ``Continue? [y/N]:``
    are shown at once; :meth:`send` answers them. The child runs in its
    own process group: :meth:`cancel` sends SIGINT first (the command
    finishes cleanly, like Ctrl+C), a second call SIGTERM.

    Attributes:
        program: Words that start a command, by default this interpreter
            with ``-m mediatools``.
        process: The running child, or None.
        output: Pieces of output not yet fetched by :meth:`poll`.
        cancels: Number of :meth:`cancel` calls for the current child.
        reader: Thread that reads the child's output.
    """

    def __init__(self, program: list[str] | None = None):
        """Prepare a runner.

        Args:
            program: Words that start a command; ``[python, -m, mediatools]`` if None.
        """
        self.program = program or [sys.executable, "-m", "mediatools"]
        self.process: subprocess.Popen[bytes] | None = None
        self.output: queue.Queue[str] = queue.Queue()
        self.cancels = 0
        self.reader: threading.Thread | None = None

    @property
    def running(self) -> bool:
        """True while a child runs."""
        return self.process is not None and self.process.poll() is None

    def start(self, args: list[str]) -> None:
        """Start a command.

        Args:
            args: Words after the program, e.g. ``["shrink", "-y"]``.

        Raises:
            RuntimeError: If a command of this runner still runs.
        """
        if self.running:
            raise RuntimeError("a command is still running")
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        self.cancels = 0
        self.process = subprocess.Popen(  # pylint: disable=consider-using-with
            self.program + args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, env=env, start_new_session=True)
        self.reader = threading.Thread(target=self._read, args=(self.process,), daemon=True)
        self.reader.start()

    def _read(self, process: subprocess.Popen[bytes]) -> None:
        """Move the output of a child into :attr:`output` (reader thread).

        Args:
            process: The child.
        """
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        stream = process.stdout
        if stream is None:
            return
        while True:
            chunk = os.read(stream.fileno(), 4096)
            if not chunk:
                break
            self.output.put(decoder.decode(chunk))
        rest = decoder.decode(b"", final=True)
        if rest:
            self.output.put(rest)
        stream.close()

    def poll(self) -> tuple[str, int | None]:
        """Fetch new output and the exit code.

        Returns:
            New text (possibly empty) and the exit code, or None while the
            command runs or its output is not completely read yet.
        """
        pieces = []
        while True:
            try:
                pieces.append(self.output.get_nowait())
            except queue.Empty:
                break
        code = None
        if self.process is not None and self.process.poll() is not None \
                and (self.reader is None or not self.reader.is_alive()) and self.output.empty():
            code = self.process.returncode
            if self.process.stdin is not None:
                self.process.stdin.close()
        return "".join(pieces), code

    def send(self, text: str) -> None:
        """Answer a question of the running command.

        Args:
            text: The answer; a line end is added.

        Raises:
            RuntimeError: If no command runs.
        """
        if not self.running or self.process is None or self.process.stdin is None:
            raise RuntimeError("no command is running")
        self.process.stdin.write((text + "\n").encode("utf-8"))
        self.process.stdin.flush()

    def cancel(self) -> str:
        """Ask the running command to end.

        Returns:
            What was done, for the GUI.
        """
        if not self.running or self.process is None:
            return "no command is running"
        self.cancels += 1
        sig = signal.SIGINT if self.cancels == 1 else signal.SIGTERM
        try:
            os.killpg(self.process.pid, sig)
        except ProcessLookupError:
            return "the command has already ended"
        return ("interrupt sent (like Ctrl+C); press again to terminate" if sig == signal.SIGINT
                else "terminate signal sent")

    def wait(self, timeout: float | None = None) -> int | None:
        """Wait for the child and its output (used by tests and on exit).

        Args:
            timeout: Seconds to wait at most; None waits without limit.

        Returns:
            The exit code, or None if no command was started.
        """
        if self.process is None:
            return None
        code = self.process.wait(timeout)
        if self.reader is not None:
            self.reader.join(timeout)
        if self.process.stdin is not None:
            self.process.stdin.close()
        return code
