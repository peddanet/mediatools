#!/usr/bin/env python3
"""Generate UML class diagrams and the API reference from the source code.

Output (relative to the repository root):

* ``docs/img/uml_<package>.mmd`` - Mermaid class diagram per sub-package,
  attributes taken from the ``Attributes:`` section of each class docstring,
  methods from the class body.
* ``docs/api.md`` - API reference with signatures and Google-style
  docstrings of every public and private class member.

Run from the repository root::

    python3 tools/gen_docs.py
"""
from __future__ import annotations

import importlib
import inspect
import os
import pkgutil
import re
import sys
from collections.abc import Callable

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

PACKAGES = ("core", "shrink", "triage", "flatten", "sync", "snapshot")


class DocGenerator:
    """Collects classes of the mediatools package and renders documentation.

    Attributes:
        classes: Mapping sub-package name to list of ``(module, class)``.
    """

    def __init__(self) -> None:
        """Import all modules and collect the classes defined in them."""
        self.classes: dict[str, list[tuple[str, type]]] = {}
        base = importlib.import_module("mediatools")
        for info in pkgutil.walk_packages(base.__path__, "mediatools."):
            if info.name.endswith("__main__"):
                continue      # importing it would run the CLI
            module = importlib.import_module(info.name)
            for _name, cls in inspect.getmembers(module, inspect.isclass):
                if cls.__module__ == module.__name__:
                    package = info.name.split(".")[1] if info.name.count(".") > 1 else "cli"
                    self.classes.setdefault(package, []).append((info.name, cls))

    @staticmethod
    def attributes(klass: type) -> list[str]:
        """Read attribute names from the ``Attributes:`` docstring section.

        Args:
            klass: Class to inspect.

        Returns:
            Attribute names in docstring order.
        """
        doc = inspect.getdoc(klass) or ""
        match = re.search(r"^Attributes:\n((?:    .*\n?|\n)*)", doc, re.MULTILINE)
        if not match:
            return []
        return re.findall(r"^    (\w+):", match.group(1), re.MULTILINE)

    @staticmethod
    def methods(klass: type) -> list[tuple[str, object]]:
        """List the functions defined directly in a class.

        Args:
            klass: Class to inspect.

        Returns:
            ``(name, function)`` pairs in source order.
        """
        out = []
        for name, value in klass.__dict__.items():
            func = value.__func__ if isinstance(value, (staticmethod, classmethod)) else value
            if isinstance(value, property):
                func = value.fget
            if inspect.isfunction(func):
                out.append((name, func))
        return out

    @staticmethod
    def _source(obj: object) -> str:
        """Return the source code of an object, or an empty string.

        Args:
            obj: Function or class.

        Returns:
            Source text; empty for generated code (e.g. dataclass ``__init__``).
        """
        try:
            return inspect.getsource(obj) if obj is not None else ""
        except (OSError, TypeError):
            return ""

    def mermaid(self, package: str) -> str:
        """Render the class diagram of one sub-package.

        Args:
            package: Sub-package name, e.g. ``sync``.

        Returns:
            Mermaid ``classDiagram`` source.
        """
        lines = ["classDiagram", "  direction LR"]
        names = {cls.__name__ for _m, cls in self.classes.get(package, [])}
        for _module, cls in self.classes.get(package, []):
            lines.append(f"  class {cls.__name__} {{")
            for attr in self.attributes(cls):
                lines.append(f"    +{attr}")
            for name, func in self.methods(cls):
                if name.startswith("__") and name not in ("__init__", "__iter__"):
                    continue
                visibility = "-" if name.startswith("_") and not name.startswith("__") else "+"
                params = [p for p in inspect.signature(func).parameters if p not in ("self", "cls")]
                label = name.strip("_") if name.startswith("__") else name
                lines.append(f"    {visibility}{label}({', '.join(params)})")
            lines.append("  }")
        # edges: "-->" collaborator held by the constructor, "..>" used in methods
        for _module, cls in self.classes.get(package, []):
            init_src = self._source(cls.__dict__.get("__init__"))
            body_src = self._source(cls)
            for other in sorted(names - {cls.__name__}):
                if re.search(rf"\b{other}\b", init_src):
                    lines.append(f"  {cls.__name__} --> {other}")
                elif re.search(rf"\b{other}\b", body_src):
                    lines.append(f"  {cls.__name__} ..> {other}")
        return "\n".join(lines) + "\n"

    def api_markdown(self) -> str:
        """Render the API reference.

        Returns:
            Markdown text.
        """
        out = ["# Appendix: API reference", "",
               "Generated by `tools/gen_docs.py` from the docstrings "
               "(Google style). Do not edit by hand.", ""]
        for package in ("cli", *PACKAGES):
            out.append(f"## `mediatools.{package}`" if package != "cli"
                       else "## `mediatools.cli`")
            out.append("")
            for module, cls in self.classes.get(package, []):
                out.append(f"### `{cls.__name__}` (`{module}`) {{.unnumbered}}")
                out.append("")
                out.append("~~~text")
                out.append(inspect.getdoc(cls) or "")
                out.append("~~~")
                out.append("")
                for name, func in self.methods(cls):
                    out.append(f"**`{name}`**")
                    out.append("")
                    out.append("~~~text")
                    out.append(self.signature(name, func))
                    out.append("")
                    out.append(inspect.getdoc(func) or "")
                    out.append("~~~")
                    out.append("")
        return "\n".join(out)

    @staticmethod
    def _default(value: object) -> str:
        """Render a parameter default in a short, reproducible form.

        Args:
            value: Default value.

        Returns:
            Function name instead of ``<function … at 0x…>``; ``frozenset(…)``
            instead of the (unordered, long) element list.
        """
        if callable(value):
            return getattr(value, "__qualname__", "callable")
        if isinstance(value, frozenset):
            return "frozenset(...)"
        return repr(value)

    @classmethod
    def signature(cls, name: str, func: Callable[..., object]) -> str:
        """Render a signature, one parameter per line if it is long.

        Args:
            name: Function name.
            func: Function object.

        Returns:
            Signature text; lines stay below 90 characters where possible.
        """
        sig = inspect.signature(func)
        params = []
        for param in sig.parameters.values():
            text = param.name
            if param.kind is param.VAR_POSITIONAL:
                text = "*" + text
            elif param.kind is param.VAR_KEYWORD:
                text = "**" + text
            if param.annotation is not param.empty:
                annotation = param.annotation
                text += ": " + (annotation if isinstance(annotation, str)
                                else inspect.formatannotation(annotation))
            if param.default is not param.empty:
                separator = "=" if param.annotation is param.empty else " = "
                text += separator + cls._default(param.default)
            params.append(text)
        returns = ""
        if sig.return_annotation is not sig.empty:
            ret = sig.return_annotation
            returns = " -> " + (ret if isinstance(ret, str) else inspect.formatannotation(ret))
        one_line = f"{name}({', '.join(params)}){returns}"
        if len(one_line) <= 88:
            return one_line
        body = ",\n".join("    " + p for p in params)
        return f"{name}(\n{body},\n){returns}"

    def write(self) -> None:
        """Write all Mermaid files and the API reference."""
        img = os.path.join(ROOT, "docs", "img")
        os.makedirs(img, exist_ok=True)
        for package in ("cli", *PACKAGES):
            with open(os.path.join(img, f"uml_{package}.mmd"), "w", encoding="utf-8") as handle:
                handle.write(self.mermaid(package))
        with open(os.path.join(ROOT, "docs", "api.md"), "w", encoding="utf-8") as handle:
            handle.write(self.api_markdown())


if __name__ == "__main__":
    DocGenerator().write()
