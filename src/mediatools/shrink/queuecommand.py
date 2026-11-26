"""The ``queue`` command: shrink jobs that run one after the other."""
from __future__ import annotations

import argparse
import os
import shlex
from collections.abc import Callable

from mediatools.core.runcontrol import RunControl
from mediatools.shrink.shrinkqueue import ShrinkQueue


class QueueCommand:
    """Lists and changes the shrink queue and starts it.

    Without options the queue is listed. ``--add`` takes the arguments of
    ``mediatools shrink`` as one text (folder first, e.g. ``"~/Videos
    --min-seconds 120 --jobs 2"``); they are checked with the parser of
    ``shrink`` before they are stored, and the folder is stored as an
    absolute path; ``--foreground`` and ``-y``/``--yes`` are dropped (a
    queued job runs in the background and is started without asking).
    ``--remove``, ``--move``/``--to`` and ``--clear`` change
    the list; ``--run`` starts the first job when no shrink runs. Every
    change prints the list afterwards.

    Attributes:
        queue: The queue.
        validate: Checks shrink arguments (raises :class:`SystemExit` on an
            error, as argparse does).
        start: Runs ``mediatools shrink`` with arguments and returns its
            exit code.
    """

    DROPPED = ("--foreground", "-y", "--yes")

    def __init__(self, queue: ShrinkQueue, validate: Callable[[list[str]], object],
                 start: Callable[[list[str]], int],
                 running: Callable[[], int | None] | None = None) -> None:
        """Wire the command.

        Args:
            queue: The queue.
            validate: Checks shrink arguments.
            start: Runs ``mediatools shrink`` with arguments.
            running: Returns the PID of the running shrink or None (see
                :meth:`ShrinkQueue.recover`); the shrink's PID file if None.
        """
        self.queue = queue
        self.validate = validate
        self.start = start
        self.running = running or RunControl("shrink").active_pid

    @staticmethod
    def add_parser(sub: argparse._SubParsersAction) -> None:
        """Define the subcommand.

        Args:
            sub: Sub-parser collection of the main parser.
        """
        cmd = sub.add_parser("queue", help="shrink jobs that run one after the other: list, "
                                           "add, move, remove, start")
        cmd.add_argument("--add", metavar="ARGS",
                         help='arguments of "mediatools shrink" as one text, folder first, '
                              'e.g. "~/Videos --min-seconds 120 --jobs 2"')
        cmd.add_argument("--note", default="", help="with --add: a note shown in the list")
        cmd.add_argument("--first", action="store_true",
                         help="with --add: put the job first instead of last")
        cmd.add_argument("--remove", type=int, metavar="N", help="remove job number N")
        cmd.add_argument("--move", type=int, metavar="N", help="move job number N (with --to)")
        cmd.add_argument("--to", type=int, metavar="M", help="with --move: new position")
        cmd.add_argument("--clear", action="store_true", help="remove every job")
        cmd.add_argument("--run", action="store_true",
                         help="start the first job now (when no shrink runs)")

    def run(self, args: argparse.Namespace) -> int:
        """Carry out the options and print the queue.

        Args:
            args: Parsed arguments.

        Returns:
            0; 2 for invalid input; for ``--run`` the exit code of the
            started job (3 if another shrink runs, the job stays first).
        """
        if self.queue.recover(self.running()):
            print("queue: a job left by an interrupted run is back as first entry (paused)")
        try:
            self.change(args)
        except ValueError as err:
            print(f"Error: {err}")
            return 2
        if args.run:
            return self.run_first()
        print("\n".join(self.queue.lines()))
        return 0

    def change(self, args: argparse.Namespace) -> None:
        """Apply ``--add``, ``--remove``, ``--move`` and ``--clear``.

        Args:
            args: Parsed arguments.

        Raises:
            ValueError: For invalid input.
        """
        if args.clear:
            print(f"{self.queue.clear()} job(s) removed")
        if args.remove is not None:
            entry = self.queue.remove(args.remove)
            print(f"removed: mediatools shrink {shlex.join(entry['args'])}")
        if args.move is not None:
            if args.to is None:
                raise ValueError("--move needs --to")
            self.queue.move(args.move, args.to)
        if args.add:
            position = self.queue.add(self.checked(args.add), args.note, args.first)
            print(f"added as job {position}")

    def checked(self, text: str) -> list[str]:
        """Check shrink arguments and make the folder absolute.

        Args:
            text: Arguments as one text, folder first.

        Returns:
            The arguments with an absolute folder.

        Raises:
            ValueError: If they are not valid for ``shrink`` or the folder
                is missing.
        """
        try:
            args = shlex.split(text)
        except ValueError as err:
            raise ValueError(f"--add: {err}") from None
        if not args or args[0].startswith("-"):
            raise ValueError("--add: the folder must come first")
        args = [arg for arg in args if arg not in self.DROPPED]
        args[0] = os.path.abspath(os.path.expanduser(args[0]))
        if not os.path.isdir(args[0]):
            raise ValueError(f"--add: not a folder: {args[0]}")
        try:
            self.validate(args)
        except SystemExit:
            raise ValueError("--add: not valid for 'mediatools shrink' (see above)") from None
        return args

    def run_first(self) -> int:
        """Start the first job of the queue.

        Returns:
            Exit code of the job; 0 if the queue is empty; 3 if another
            shrink runs (the job is put back first).
        """
        entry = self.queue.pop()
        if entry is None:
            print("queue: empty - nothing to start")
            return 0
        print(f"starting: mediatools shrink {shlex.join(entry['args'])}")
        code = self.start([*entry["args"], "-y"])
        if code == 3:
            self.queue.take_current()
            self.queue.add(entry["args"], entry.get("note", ""), front=True)
            print("the job stays first in the queue; it starts when the running job ends")
        return code
