"""Release 1.9.1: caches shared by processes running at the same time."""
import os
from pathlib import Path

from conftest import make_file
from test_release_170 import logger
from mediatools.broken import Assessment, AssessmentStore
from mediatools.core.codeccache import CodecCache
from mediatools.core.durationcache import DurationCache


def test_two_processes_keep_each_others_durations(tmp_path: Path) -> None:
    """Saving merges what the other instance saved; broken lines are skipped.

    Args:
        tmp_path: Temporary directory.
    """
    file = str(tmp_path / "d.tsv")
    one, two = DurationCache(file), DurationCache(file)
    first = make_file(tmp_path / "a.mp4")
    second = make_file(tmp_path / "b.mp4")
    one.put(first, os.stat(first), 10.0)
    two.put(second, os.stat(second), 20.0)
    one.save()
    two.save()
    assert set(DurationCache(file).entries) == {first, second}
    with open(file, "a", encoding="utf-8") as handle:
        handle.write("x\tnot-a-number\t1\t2\nshort line\n")
    assert len(DurationCache(file).entries) == 2
    assert not [name for name in os.listdir(tmp_path) if name.endswith(".tmp")]
    codecs = CodecCache(str(tmp_path / "c.tsv"))
    codecs.put(first, os.stat(first), "h264")
    codecs.save()
    assert CodecCache(str(tmp_path / "c.tsv")).get(first, os.stat(first)) == "h264"


def test_two_broken_runs_keep_each_others_measurements(tmp_path: Path) -> None:
    """The stored measurements of two stores on one file are merged.

    Args:
        tmp_path: Temporary directory.
    """
    file = str(tmp_path / "state" / AssessmentStore.FILE)
    first = make_file(tmp_path / "a.mp4", b"aa")
    second = make_file(tmp_path / "b.mp4", b"bb")
    one = AssessmentStore(file, logger(tmp_path))
    two = AssessmentStore(file, logger(tmp_path))
    one.put(first, Assessment(True, 10, 10))
    two.put(second, Assessment(True, 10, 2, errors=3))
    again = AssessmentStore(file, logger(tmp_path))
    assert again.stored(first) is not None and again.stored(second) is not None
