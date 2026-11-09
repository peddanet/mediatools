"""DecodeCheck: trial decode that finds videos whose data cannot be decoded."""
import logging
import subprocess
from pathlib import Path

import pytest

from conftest import make_file
from test_shrink_run import build, videos
from mediatools.core.decodecheck import DecodeCheck
from mediatools.shrink import ShrinkDatabase
from mediatools.shrink.fileresult import FileResult

KEY = "00112233445566778899aabbccddeeff"
KID = "0123456789abcdef0123456789abcdef"


def ffmpeg(*args: str) -> None:
    """Run ffmpeg quietly and fail the test on an error.

    Args:
        *args: ffmpeg arguments after the log options.
    """
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", *args], check=True)


@pytest.fixture(name="clips", scope="module")
def fixture_clips(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    """Create a healthy clip and one whose data stays encrypted.

    The second clip is encrypted with Common Encryption (cenc-aes-ctr) and
    then copied without the key: the result is tagged ``avc1``/``mp4a``
    like a normal file, but its sample data is still encrypted - the case
    found in the collection.

    Args:
        tmp_path_factory: Factory for a module-wide temporary directory.

    Returns:
        Paths ``good`` and ``encrypted``.
    """
    base = tmp_path_factory.mktemp("clips")
    good, enc, stripped = (str(base / name) for name in ("good.mp4", "enc.mp4", "bad.mp4"))
    ffmpeg("-f", "lavfi", "-i", "testsrc=d=3:s=320x240", "-f", "lavfi", "-i", "sine=d=3",
           "-c:v", "libx264", "-c:a", "aac", "-shortest", good)
    ffmpeg("-i", good, "-c", "copy", "-encryption_scheme", "cenc-aes-ctr",
           "-encryption_key", KEY, "-encryption_kid", KID, enc)
    subprocess.run(["ffmpeg", "-v", "quiet", "-nostdin", "-y", "-i", enc, "-c", "copy",
                    stripped], check=True)
    return {"good": good, "encrypted": stripped}


def test_healthy_clip_is_usable(clips: dict[str, str]) -> None:
    """A normal video decodes without errors.

    Args:
        clips: Test clips (fixture).
    """
    check = DecodeCheck()
    assert check.errors(clips["good"]) == 0
    assert check.unusable(clips["good"]) is None


def test_encrypted_clip_is_unusable(clips: dict[str, str]) -> None:
    """Still-encrypted data is found although the tags look normal.

    Args:
        clips: Test clips (fixture).
    """
    tags = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                           "stream=codec_tag_string", "-of", "csv=p=0", clips["encrypted"]],
                          capture_output=True, text=True, check=True).stdout.split()
    assert tags == ["avc1", "mp4a"]
    errors = DecodeCheck().unusable(clips["encrypted"])
    assert errors is not None and errors >= DecodeCheck.MAX_ERRORS


def test_command_reads_only_the_start() -> None:
    """The trial is limited with -t before -i and writes nothing."""
    cmd = DecodeCheck(seconds=2.5).command("/x/v.mp4")
    assert cmd[cmd.index("-t") + 1] == "2.5" and cmd.index("-t") < cmd.index("-i")
    assert cmd[-2:] == ["null", "-"] and "-v" in cmd


def test_unknown_result_never_blocks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Missing ffmpeg or a timeout gives no verdict.

    Args:
        monkeypatch: pytest fixture.
        tmp_path: Temporary directory (fixture).
    """
    path = make_file(tmp_path / "v.mp4", b"x")
    assert DecodeCheck(ffmpeg="no-such-ffmpeg-binary").unusable(path) is None

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("ffmpeg", 1)

    monkeypatch.setattr(subprocess, "run", timeout)
    assert DecodeCheck().errors(path) is None


@pytest.mark.parametrize("kwargs", [{"seconds": 0}, {"timeout": -1}, {"max_errors": 0}])
def test_invalid_settings(kwargs: dict) -> None:
    """Nonsense settings are refused.

    Args:
        kwargs: One invalid setting.
    """
    with pytest.raises(ValueError):
        DecodeCheck(**kwargs)


class FakeCheck:
    """DecodeCheck double: files named ``medium.mp4`` are unusable."""

    seconds = 10.0

    def unusable(self, path: str) -> int | None:
        """Report the double's verdict.

        Args:
            path: Video file.

        Returns:
            42 errors for ``medium.mp4``, otherwise None.
        """
        return 42 if path.endswith("medium.mp4") else None


def test_shrinker_sets_broken_videos_aside(tmp_path: Path,
                                           caplog: pytest.LogCaptureFixture) -> None:
    """An undecodable video is neither encoded nor recorded nor changed.

    Args:
        tmp_path: Temporary directory (fixture).
        caplog: pytest fixture for log records.
    """
    paths = videos(tmp_path)
    shrinker, encoder = build(tmp_path)
    shrinker.state["decode"] = FakeCheck()  # type: ignore[typeddict-item]
    rows: list[FileResult] = []
    # pylint: disable-next=protected-access
    shrinker._result = rows.append  # type: ignore[method-assign]
    with caplog.at_level(logging.INFO, logger="test"):
        assert shrinker.run() == 0
    assert encoder.calls == ["large.mp4", "small.mp4"]
    assert Path(paths["medium.mp4"]).read_bytes() == b"m" * 100
    assert not ShrinkDatabase(str(tmp_path / "c/a")).is_done(paths["medium.mp4"])
    assert shrinker.stats["broken"] == 1 and shrinker.found["broken"] == [paths["medium.mp4"]]
    assert [row.status for row in rows if row.path == paths["medium.mp4"]] == ["BROKEN"]
    assert "not decodable: 1" in caplog.text and "BROKEN (not decodable, kept)" in caplog.text
