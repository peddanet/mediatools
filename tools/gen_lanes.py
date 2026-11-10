#!/usr/bin/env python3
"""Draw the git-flow branches as parallel lanes with every gate run marked.

Output (relative to the repository root): ``docs/img/lanes_release.svg``
and ``docs/img/lanes_hotfix.svg``. Each branch is a vertical lane, time
runs downwards, a horizontal arrow shows what goes from which branch to
which, and a badge on the right says which quality-gate run belongs to
the step (local pre-push hook, CI run on GitHub, or none).

Run from the repository root (normal user)::

    python3 tools/gen_lanes.py
    cd docs/img && for f in lanes_*.svg; do rsvg-convert -z 2 "$f" -o "${f%.svg}.png"; done
"""
from __future__ import annotations

import os
from html import escape

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BADGES = {                 # kind: (fill, stroke, text colour)
    "local": ("#E1F5EE", "#0F6E56", "#04342C"),
    "ci": ("#EEEDFE", "#534AB7", "#26215C"),
    "none": ("#F1EFE8", "#888780", "#444441"),
}

RELEASE = {
    "lanes": [("main", "#185FA5", 0, 10), ("release/1.3.0", "#BA7517", 5, 7),
              ("develop", "#0F6E56", 0, 10), ("feature/x", "#993556", 1, 4)],
    "rows": [
        ("dot", "main", "main", "main (= former master): released versions", "", ""),
        ("branch", "develop", "feature/x", "new branch feature/x from develop", "", ""),
        ("dot", "feature/x", "feature/x", "commits, then git push",
         "local", "LOCAL"),
        ("pr", "feature/x", "develop", "pull request feature/x -> develop", "ci", "CI"),
        ("merge", "feature/x", "develop", "merge into develop (CI was green)", "none",
         "none"),
        ("branch", "develop", "release/1.3.0",
         "new branch release/1.3.0; version; push",
         "local", "LOCAL"),
        ("pr", "release/1.3.0", "main", "pull request release/1.3.0 -> main", "ci", "CI"),
        ("merge", "release/1.3.0", "main", "merge into main (CI was green)", "none", "none"),
        ("dot", "main", "main", "tag v1.3.0 pushed: + release-check", "ci",
         "CI"),
        ("pr", "main", "develop", "pull request main -> develop", "ci", "CI"),
        ("merge", "main", "develop", "merge into develop (CI was green)", "none", "none"),
    ],
}

HOTFIX = {
    "lanes": [("main", "#185FA5", 0, 10), ("hotfix/1.2.13", "#A32D2D", 1, 4),
              ("merge/hotfix-1.2.13", "#534AB7", 6, 9), ("develop", "#0F6E56", 0, 10)],
    "rows": [
        ("dot", "main", "main", "release 1.2.12 (tag v1.2.12)", "", ""),
        ("branch", "main", "hotfix/1.2.13", "2: new branch hotfix/1.2.13 from main", "", ""),
        ("dot", "hotfix/1.2.13", "hotfix/1.2.13", "2-3: git am patch, git push",
         "local", "LOCAL"),
        ("pr", "hotfix/1.2.13", "main", "4: pull request hotfix -> main", "ci",
         "CI 1/3"),
        ("merge", "hotfix/1.2.13", "main", "5: merge into main (CI 1/3 green)",
         "none", "none"),
        ("dot", "main", "main", "6: tag v1.2.13 pushed: + release-check", "ci",
         "CI 2/3"),
        ("branch", "main", "merge/hotfix-1.2.13",
         "7: new branch merge/hotfix from main", "", ""),
        ("merge", "develop", "merge/hotfix-1.2.13",
         "7: merge develop in, git push", "local",
         "LOCAL"),
        ("pr", "merge/hotfix-1.2.13", "develop",
         "7: pull request -> develop", "ci", "CI 3/3"),
        ("merge", "merge/hotfix-1.2.13", "develop",
         "7: merge into develop (CI 3/3 green)",
         "none", "none"),
        ("dot", "main", "main", "8: install.sh installs v1.2.13", "", ""),
    ],
}


class LaneDiagram:
    """Render one flow as SVG: branches as lanes, steps as rows.

    Attributes:
        flow: Lane and row definitions (see ``RELEASE``).
        lane_x: x coordinate per lane name.
        parts: SVG fragments collected while drawing.
    """

    LANE_GAP = 165
    LEFT = 90
    LANE_Y = 120
    ROW = 52
    WIDTH = 1120

    def __init__(self, flow: dict) -> None:
        """Place the lanes.

        Args:
            flow: Lane and row definitions.
        """
        self.flow = flow
        self.lane_x = {name: self.LEFT + i * self.LANE_GAP
                       for i, (name, *_rest) in enumerate(flow["lanes"])}
        self.parts: list[str] = []

    def y(self, row: int) -> int:
        """Return the y coordinate of a row.

        Args:
            row: Row index.

        Returns:
            Centre line of the row.
        """
        return self.LANE_Y + 40 + row * self.ROW

    def text(self, x: float, y: float, value: str, **style: str) -> None:
        """Add a text element.

        Args:
            x: Anchor x.
            y: Baseline y.
            value: Text.
            **style: Further SVG attributes (underscores become dashes).
        """
        attrs = " ".join(f'{key.replace("_", "-")}="{val}"' for key, val in style.items())
        self.parts.append(f'<text x="{x}" y="{y}" {attrs}>{escape(value)}</text>')

    def lanes(self) -> None:
        """Draw the lane headers and the life line of every branch."""
        for index, (name, colour, first, last) in enumerate(self.flow["lanes"]):
            x = self.lane_x[name]
            width = 10 * len(name) + 24
            label_y = self.LANE_Y - 44 - 34 * (index % 2)     # staggered: long names may overlap
            self.parts.append(f'<rect x="{x - width / 2}" y="{label_y}" width="{width}" '
                              f'height="28" rx="6" fill="{colour}"/>')
            self.text(x, label_y + 19, name, fill="#FFFFFF", text_anchor="middle",
                      font_weight="bold")
            self.parts.append(f'<line x1="{x}" y1="{label_y + 28}" x2="{x}" y2="{self.LANE_Y}" '
                              f'stroke="{colour}"/>')
            self.parts.append(f'<line x1="{x}" y1="{self.LANE_Y}" x2="{x}" '
                              f'y2="{self.y(len(self.flow["rows"]) - 1) + 20}" '
                              'stroke="#B4B2A9" stroke-dasharray="3 4"/>')
            self.parts.append(f'<line x1="{x}" y1="{self.y(first)}" x2="{x}" '
                              f'y2="{self.y(last)}" stroke="{colour}" stroke-width="6"/>')

    def row(self, index: int, item: tuple[str, str, str, str, str, str]) -> None:
        """Draw one step: arrow or dot, description, badge.

        Args:
            index: Row index.
            item: ``(kind, from lane, to lane, description, badge kind, badge text)``.
        """
        kind, src, dst, label, badge, badge_text = item
        y = self.y(index)
        x1, x2 = self.lane_x[src], self.lane_x[dst]
        if kind != "dot":
            dash = ' stroke-dasharray="7 5"' if kind == "pr" else ""
            end = x2 - 12 if x2 > x1 else x2 + 12
            self.parts.append(f'<line x1="{x1}" y1="{y}" x2="{end}" y2="{y}" stroke="#2C2C2A" '
                              f'stroke-width="2"{dash} marker-end="url(#arrow)"/>')
        if kind != "pr":
            self.parts.append(f'<circle cx="{x2}" cy="{y}" r="9" fill="#FFFFFF" '
                              'stroke="#2C2C2A" stroke-width="2"/>')
        column = self.LEFT + (len(self.flow["lanes"]) - 1) * self.LANE_GAP + 60
        if badge:
            fill, stroke, colour = BADGES[badge]
            width = 86
            self.parts.append(f'<rect x="{column}" y="{y - 13}" width="{width}" height="26" '
                              f'rx="13" fill="{fill}" stroke="{stroke}"/>')
            self.text(column + width / 2, y + 5, badge_text, fill=colour, text_anchor="middle",
                      font_weight="bold")
            column += width + 12
        self.text(column, y + 5, label, fill="#2C2C2A")

    def svg(self) -> str:
        """Return the complete SVG document.

        Returns:
            SVG source.
        """
        self.parts = []
        self.lanes()
        for index, item in enumerate(self.flow["rows"]):
            self.row(index, item)
        height = self.y(len(self.flow["rows"])) + 30
        marker = ('<defs><marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" '
                  'markerWidth="7" markerHeight="7" orient="auto">'
                  '<path d="M0 0L10 5L0 10z" fill="#2C2C2A"/></marker></defs>')
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.WIDTH}" '
                f'height="{height}" font-family="DejaVu Sans" font-size="16">'
                f'<rect width="{self.WIDTH}" height="{height}" fill="#FFFFFF"/>{marker}'
                + "".join(self.parts) + "</svg>\n")


def main() -> None:
    """Write both lane diagrams to ``docs/img``."""
    for stem, flow in (("lanes_release", RELEASE), ("lanes_hotfix", HOTFIX)):
        path = os.path.join(ROOT, "docs", "img", f"{stem}.svg")
        with open(path, "w", encoding="utf-8") as out:
            out.write(LaneDiagram(flow).svg())
        print(path)


if __name__ == "__main__":
    main()
