"""One planned or executed action of a flatten run."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PlanEntry:
    """One action of a flatten run.

    Attributes:
        action: ``link`` (new), ``relink`` (source replaced, link renewed),
            ``prune`` (orphan link removed), ``ok`` (already present) or
            ``skip`` (cannot be linked, see ``note``).
        unit: Name of the unit directory.
        link: Absolute path of the link in ``_flat``.
        source: Absolute path of the source file.
        level: Naming level 0..3 of the link name.
        note: Free text, e.g. the reason for ``skip``.
    """

    action: str
    unit: str
    link: str
    source: str
    level: int = 0
    note: str = ""
