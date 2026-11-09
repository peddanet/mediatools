"""Planned and executed flatten actions."""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from typing import ClassVar

from mediatools.flatten.planentry import PlanEntry


@dataclass
class FlatPlan:
    """Collection of :class:`PlanEntry` with summary and TSV persistence.

    The TSV file written by :meth:`save` is also the input for
    ``mediatools flatten --undo PLAN``.

    Attributes:
        entries: All planned or executed actions.
    """

    entries: list[PlanEntry] = field(default_factory=list)
    FIELDS: ClassVar[tuple[str, ...]] = ("action", "unit", "link", "source", "level", "note")

    def add(self, entry: PlanEntry) -> None:
        """Append an entry.

        Args:
            entry: Action to add.
        """
        self.entries.append(entry)

    def by_action(self, action: str) -> list[PlanEntry]:
        """Select entries of one kind.

        Args:
            action: Action name, e.g. ``link``.

        Returns:
            Matching entries in plan order.
        """
        return [entry for entry in self.entries if entry.action == action]

    def counts(self) -> dict[str, int]:
        """Count entries per action.

        Returns:
            Mapping action name to number of entries.
        """
        out: dict[str, int] = {}
        for entry in self.entries:
            out[entry.action] = out.get(entry.action, 0) + 1
        return out

    def level_counts(self) -> dict[int, int]:
        """Count new and renewed links per naming level.

        Returns:
            Mapping level to number of ``link``/``relink`` entries.
        """
        out: dict[int, int] = {}
        for entry in self.entries:
            if entry.action in ("link", "relink"):
                out[entry.level] = out.get(entry.level, 0) + 1
        return out

    def save(self, path: str) -> None:
        """Write the plan as TSV.

        Args:
            path: Target file.
        """
        with open(path, "w", encoding="utf-8", errors="surrogateescape", newline="") as handle:
            writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
            writer.writerow(self.FIELDS)
            for entry in self.entries:
                writer.writerow([entry.action, entry.unit, entry.link, entry.source,
                                 entry.level, entry.note])

    @classmethod
    def load(cls, path: str) -> "FlatPlan":
        """Read a plan written by :meth:`save`.

        Args:
            path: Plan file.

        Returns:
            The reconstructed plan.
        """
        plan = cls()
        with open(path, encoding="utf-8", errors="surrogateescape", newline="") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                plan.add(PlanEntry(row["action"], row["unit"], row["link"], row["source"],
                                   int(row["level"] or 0), row["note"]))
        return plan
