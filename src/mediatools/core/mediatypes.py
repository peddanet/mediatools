"""Media file classification and directory names shared by all subcommands."""
from __future__ import annotations

import os


class MediaTypes:
    """Static knowledge about media file extensions and reserved directories.

    All subcommands use this class, so "what is a video" and "which
    directories are never entered" is defined in exactly one place.

    Attributes:
        FLAT_DIR: Name of the per-unit directory created by ``flatten``.
        SHRINK_TMP_DIR: Temporary output directory of ``shrink``.
        SKIP_DIRS: Directory names no subcommand descends into.
        VIDEO: Lower-case video extensions including the dot.
        IMAGE: Lower-case image extensions including the dot.
        AUDIO: Lower-case audio extensions including the dot.
        MEDIA: Union of video, image and audio extensions.
    """

    FLAT_DIR = "_flat"
    SHRINK_TMP_DIR = "newShrinkedFiles"
    SKIP_DIRS = frozenset({FLAT_DIR, SHRINK_TMP_DIR})

    VIDEO = frozenset({".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".wmv",
                       ".flv", ".ts", ".qt", ".mpg", ".mpeg"})
    IMAGE = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic",
                       ".bmp", ".tif", ".tiff"})
    AUDIO = frozenset({".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".flac"})
    MEDIA = VIDEO | IMAGE | AUDIO

    @staticmethod
    def extension(name: str) -> str:
        """Return the lower-case extension of a file name.

        Args:
            name: File name or path.

        Returns:
            Extension including the dot, e.g. ``.mp4``; empty if none.
        """
        return os.path.splitext(name)[1].lower()

    @classmethod
    def is_media(cls, name: str) -> bool:
        """Tell whether a file is a video, image or audio file.

        Args:
            name: File name or path.

        Returns:
            True if the extension belongs to :attr:`MEDIA`.
        """
        return cls.extension(name) in cls.MEDIA

    @classmethod
    def is_video(cls, name: str) -> bool:
        """Tell whether a file is a video file.

        Args:
            name: File name or path.

        Returns:
            True if the extension belongs to :attr:`VIDEO`.
        """
        return cls.extension(name) in cls.VIDEO

    @classmethod
    def parse_extensions(cls, csv: str) -> frozenset:
        """Turn a comma separated list into a set of normalised extensions.

        Args:
            csv: List such as ``"mp4,MOV, .mkv"``.

        Returns:
            Set such as ``{".mp4", ".mov", ".mkv"}``; empty items are ignored.
        """
        return frozenset("." + item.strip().lower().lstrip(".")
                         for item in csv.split(",") if item.strip())
