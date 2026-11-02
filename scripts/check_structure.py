#!/usr/bin/env python3
"""Check owned Python size targets and individually documented kernel/declaration exceptions."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Literal, TypedDict

ROOTS = ("src/focus", "scripts", "tests", "tools")
MODULE_LIMIT = 300
CALLABLE_LIMIT = 40
RULES = Path("docs/development/structure_exceptions.json")


class ExceptionRule(TypedDict):
    """Reviewed maximum size and the specific reason a named unit stays together."""

    max_lines: int
    category: Literal["kernel", "declaration"]
    reason: str


def read_rules(path: Path) -> dict[str, ExceptionRule]:
    """Read the exception inventory; reject malformed or undocumented entries."""
    raw: object = json.loads(path.read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected an object keyed by path::qualified_name")
    rules: dict[str, ExceptionRule] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            raise ValueError(f"{path}: invalid exception entry")
        size, category, reason = (
            value.get("max_lines"),
            value.get("category"),
            value.get("reason"),
        )
        if not isinstance(size, int) or size <= CALLABLE_LIMIT:
            raise ValueError(f"{key}: max_lines must exceed the ordinary target")
        if category not in ("kernel", "declaration"):
            raise ValueError(f"{key}: only kernel/declaration exceptions are allowed")
        if not isinstance(reason, str) or len(reason.strip()) < 30:
            raise ValueError(f"{key}: document the specific reason for the exception")
        rules[key] = {"max_lines": size, "category": category, "reason": reason}
    return rules


def substantive_lines(source: str, tree: ast.Module) -> set[int]:
    """Return nonblank, noncomment source lines, excluding all definition docstrings."""
    docs: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            if ast.get_docstring(node):
                doc = node.body[0]
                docs.update(range(doc.lineno, (doc.end_lineno or doc.lineno) + 1))
    return {
        number
        for number, line in enumerate(source.splitlines(), 1)
        if line.strip() and not line.lstrip().startswith("#") and number not in docs
    }


def callable_sizes(node: ast.AST, lines: set[int], prefix: str = "") -> dict[str, int]:
    """Measure each callable, including nested declarations in their enclosing callable."""
    sizes: dict[str, int] = {}
    for child in ast.iter_child_nodes(node):
        name = prefix
        if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            name = f"{prefix}.{child.name}".lstrip(".")
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            sizes[name] = sum(
                number in lines
                for number in range(
                    child.lineno, (child.end_lineno or child.lineno) + 1
                )
            )
        sizes.update(callable_sizes(child, lines, name))
    return sizes


def measured_sizes() -> dict[str, tuple[int, int]]:
    """Map every owned module/callable key to its measured size and ordinary limit."""
    measured: dict[str, tuple[int, int]] = {}
    for root in ROOTS:
        for path in sorted(Path(root).rglob("*.py")):
            source = path.read_text()
            tree = ast.parse(source, filename=str(path))
            lines = substantive_lines(source, tree)
            measured[f"{path}::<module>"] = len(lines), MODULE_LIMIT
            for name, size in callable_sizes(tree, lines).items():
                measured[f"{path}::{name}"] = size, CALLABLE_LIMIT
    return measured


def violations(
    measured: dict[str, tuple[int, int]], rules: dict[str, ExceptionRule]
) -> list[str]:
    """Report unreviewed growth, excessive exceptions and obsolete exception entries."""
    errors: list[str] = []
    for key, (size, limit) in measured.items():
        maximum = rules[key]["max_lines"] if key in rules else limit
        if size > maximum:
            errors.append(f"{key}: {size} substantive lines exceeds {maximum}")
    for key in rules:
        if key not in measured:
            errors.append(f"{key}: exception refers to a missing definition")
        elif measured[key][0] <= measured[key][1]:
            errors.append(f"{key}: remove exception; definition now meets the target")
    return errors


def main() -> None:
    """Validate the repository inventory and exit nonzero on structural regressions."""
    measured = measured_sizes()
    rules = read_rules(RULES)
    errors = violations(measured, rules)
    if errors:
        raise SystemExit("\n".join(errors))
    modules = sum(key.endswith("::<module>") for key in measured)
    print(
        f"Structure checks passed: {modules} modules; {len(rules)} documented exceptions"
    )


if __name__ == "__main__":
    main()
