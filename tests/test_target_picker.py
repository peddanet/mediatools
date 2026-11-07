"""Tests of the interactive backup-disk choice."""
from pathlib import Path

from mediatools.core.datadir import DataDir
from mediatools.core.prompt import Prompt
from mediatools.sync.target_picker import TargetPicker


class FakeFs:
    """File-system helper with a fixed mount table.

    Attributes:
        table: Mount entries ``(device, mount point, fs type)``.
        broken: Mount points whose free space cannot be read.
    """

    def __init__(self, table: list[tuple[str, str, str]], broken: tuple[str, ...] = ()):
        """Store the mount table.

        Args:
            table: Mount entries.
            broken: Mount points for which ``free_bytes`` fails.
        """
        self.table = table
        self.broken = broken

    def mounts(self) -> list[tuple[str, str, str]]:
        """Return the mount table.

        Returns:
            The configured entries.
        """
        return self.table

    def free_bytes(self, path: str) -> int:
        """Return a fixed free space.

        Args:
            path: Mount point.

        Returns:
            Two TiB.

        Raises:
            OSError: For mount points listed as broken.
        """
        if path in self.broken:
            raise OSError("not readable")
        return 2 * 1024 ** 4


def picker(answers: list[str], table: list[tuple[str, str, str]],
           broken: tuple[str, ...] = ()) -> TargetPicker:
    """Build a picker whose questions are answered from a list.

    Args:
        answers: Answers in the order of the questions.
        table: Mount table of the fake file system.
        broken: Mount points whose free space cannot be read.

    Returns:
        The picker.
    """
    queue = list(answers)
    prompt = Prompt(input_func=lambda _question: queue.pop(0))
    return TargetPicker(prompt, FakeFs(table, broken), DataDir())


DISKS = [("/dev/sda1", "/", "ext4"), ("/dev/sdb1", "/media/u/BACKUP", "ext4"),
         ("/dev/sdb1", "/media/u/BACKUP", "ext4"), ("/dev/sdc1", "/mnt/old disk", "xfs")]


def test_candidates_only_external_and_unique() -> None:
    """Only mounts below the known prefixes are offered, each once."""
    assert picker([], DISKS).candidates() == [("/media/u/BACKUP", "ext4"),
                                              ("/mnt/old disk", "xfs")]


def test_choose_disk_and_default_target(tmp_path: Path) -> None:
    """The chosen disk plus the source name is the default target.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    chosen = picker(["1", ""], DISKS).pick(str(tmp_path / "collection"))
    assert chosen == "/media/u/BACKUP/collection"


def test_unreadable_free_space_is_still_offered(tmp_path: Path) -> None:
    """A disk whose free space cannot be read can still be chosen.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    chosen = picker(["2", "/mnt/old disk/x"], DISKS, broken=("/mnt/old disk",)).pick(
        str(tmp_path))
    assert chosen == "/mnt/old disk/x"


def test_invalid_number_aborts(tmp_path: Path) -> None:
    """A number outside the list or a non-number aborts the choice.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    assert picker(["7"], DISKS).pick(str(tmp_path)) is None
    assert picker(["abc"], DISKS).pick(str(tmp_path)) is None
    assert picker([""], DISKS).pick(str(tmp_path)) is None


def test_no_disk_manual_entry_or_abort(tmp_path: Path) -> None:
    """Without a mounted disk the target is typed in; empty input aborts.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    assert picker(["/srv/backup"], []).pick(str(tmp_path)) == "/srv/backup"
    assert picker([""], []).pick(str(tmp_path)) is None


def test_remembered_target_offered_first(tmp_path: Path) -> None:
    """A remembered target is offered; declining leads to the disk list.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    source = str(tmp_path / "src")
    picker([], DISKS).remember(source, "/media/u/BACKUP/src")
    assert picker(["y"], DISKS).pick(source) == "/media/u/BACKUP/src"
    assert picker(["n", "2", ""], DISKS).pick(source) == "/mnt/old disk/src"


def test_corrupt_state_file_is_ignored(tmp_path: Path) -> None:
    """A damaged state file counts as no remembered target.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    chooser = picker(["1", ""], DISKS)
    Path(chooser.state).write_text("{not json", encoding="utf-8")
    assert chooser.pick(str(tmp_path / "c")) == "/media/u/BACKUP/c"
