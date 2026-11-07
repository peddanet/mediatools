"""Version string that tells an installed release from a source checkout."""
from __future__ import annotations

from pathlib import Path

from mediatools import __version__


class VersionInfo:
    """Describes which mediatools is running.

    An installed release lives in a ``site-packages`` directory. Code that is
    imported straight from a clone - through ``PYTHONPATH`` or an editable
    ``pip install -e`` - lives in ``<clone>/src/mediatools`` next to the
    clone's ``pyproject.toml``. ``--version`` names the checkout in that case,
    so a development state is never mistaken for a release.

    Attributes:
        package_dir: Directory of the imported ``mediatools`` package.
        version: Version number of the imported code.
    """

    def __init__(self, package_dir: str | None = None, version: str = __version__):
        """Locate the imported package.

        Args:
            package_dir: Package directory; the imported one if None.
            version: Version number; the imported one by default.
        """
        self.package_dir = Path(package_dir or Path(__file__).resolve().parents[1])
        self.version = version

    def checkout(self) -> str | None:
        """Return the clone the code is imported from, if any.

        Returns:
            Path of the source checkout, or None for an installed package.
        """
        src = self.package_dir.parent
        if src.name == "src" and (src.parent / "pyproject.toml").is_file():
            return str(src.parent)
        return None

    def describe(self) -> str:
        """Return the text printed by ``mediatools --version``.

        Returns:
            ``"1.2.0"`` for an installed release, otherwise the version with
            the path of the source checkout.
        """
        clone = self.checkout()
        if clone is None:
            return self.version
        return f"{self.version} (source checkout {clone}, not an installed release)"
