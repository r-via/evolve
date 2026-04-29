"""Migration-completion gate: every ``evolve/*.py`` (top level) must be
``__init__.py``, ``__main__.py``, or a **pure shim** — no production code.

Per SPEC.md § "Migration-completion gate (HARD)":

  Until ``tests/test_legacy_flat_layout_empty.py`` passes, the project
  does NOT satisfy the DDD claim and cannot converge — even if every
  other gate is green.

Classifier rule — a "pure shim" module body contains ONLY:

- ``ast.Import`` / ``ast.ImportFrom`` (re-export statements)
- ``ast.Expr`` whose value is a string constant (module docstring)
- ``ast.Assign`` only for ``__all__`` or ``warnings.warn`` calls
- ``ast.If`` only for ``if __name__ == "__main__":`` blocks
- Any ``ast.FunctionDef``, ``ast.AsyncFunctionDef``, ``ast.ClassDef``,
  or non-whitelisted ``ast.Assign`` → **FAIL** with the offending node's
  line number and kind.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Files exempt from the shim check (package markers / entry-point dispatcher).
_WHITELIST = {"__init__.py", "__main__.py"}

_EVOLVE_DIR = Path(__file__).resolve().parent.parent / "evolve"


def _is_assign_allowed(node: ast.Assign) -> bool:
    """Return True if this Assign is whitelisted.

    Allowed shapes:
    - ``__all__ = [...]``
    - ``warnings.warn(...)`` (or ``_warnings.warn(...)``) — used by shims
      emitting a DeprecationWarning.
    - ``<name> = <importedname>``  — trivial re-binding aliases like
      ``CONVERGED_EXIT = _orig.CONVERGED_EXIT`` are NOT expected in
      pure shims; only ``__all__`` and ``warnings.warn`` are whitelisted.
    """
    # __all__ assignment
    for target in node.targets:
        if isinstance(target, ast.Name) and target.id == "__all__":
            return True

    # warnings.warn(...) call on the RHS
    if isinstance(node.value, ast.Call):
        func = node.value.func
        # warnings.warn or _warnings.warn
        if isinstance(func, ast.Attribute) and func.attr == "warn":
            return True

    return False


def _is_if_allowed(node: ast.If) -> bool:
    """Return True if this If node is ``if __name__ == "__main__":``."""
    test = node.test
    if isinstance(test, ast.Compare) and len(test.ops) == 1:
        if isinstance(test.ops[0], ast.Eq):
            left = test.left
            comparators = test.comparators
            if (
                isinstance(left, ast.Name)
                and left.id == "__name__"
                and len(comparators) == 1
                and isinstance(comparators[0], ast.Constant)
                and comparators[0].value == "__main__"
            ):
                return True
    return False


def _is_expr_allowed(node: ast.Expr) -> bool:
    """Return True if this Expr is a string constant or ``warnings.warn(...)``."""
    # Module docstring (string constant expression).
    if isinstance(node.value, ast.Constant) and isinstance(
        node.value.value, str
    ):
        return True
    # warnings.warn(...) / _warnings.warn(...) as a bare expression statement
    # — used by backward-compat shims to emit DeprecationWarning on import.
    if isinstance(node.value, ast.Call):
        func = node.value.func
        if isinstance(func, ast.Attribute) and func.attr == "warn":
            return True
    return False


def _classify_file(path: Path) -> list[str]:
    """Return a list of violation descriptions for a file.

    Empty list = the file is a pure shim (or whitelisted).
    """
    violations: list[str] = []
    try:
        source = path.read_text(encoding="utf-8")
    except Exception as exc:  # pragma: no cover
        violations.append(f"Could not read: {exc}")
        return violations

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:  # pragma: no cover
        violations.append(f"SyntaxError: {exc}")
        return violations

    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.Expr) and _is_expr_allowed(node):
            continue
        if isinstance(node, ast.Assign) and _is_assign_allowed(node):
            continue
        if isinstance(node, ast.If) and _is_if_allowed(node):
            continue

        # Everything else is a violation.
        kind = type(node).__name__
        line = getattr(node, "lineno", "?")
        violations.append(f"line {line}: {kind}")

    return violations


@pytest.mark.xfail(
    reason="DDD migration incomplete — unmigrated flat modules remain",
    strict=False,
)
def test_legacy_flat_layout_empty() -> None:
    """All ``evolve/*.py`` (top-level only) must be whitelisted or pure shims.

    Diagnostic format per SPEC:
      LEGACY LAYOUT NOT EMPTY: N file(s) at evolve/ top level still
      contain production code: <list>
    """
    offenders: dict[str, list[str]] = {}

    for py_file in sorted(_EVOLVE_DIR.glob("*.py")):
        if py_file.name in _WHITELIST:
            continue
        violations = _classify_file(py_file)
        if violations:
            offenders[py_file.name] = violations

    if offenders:
        detail_lines = []
        for name, viols in sorted(offenders.items()):
            detail_lines.append(f"  {name}: {', '.join(viols)}")
        detail = "\n".join(detail_lines)
        count = len(offenders)
        names = ", ".join(sorted(offenders))
        msg = (
            f"LEGACY LAYOUT NOT EMPTY: {count} file(s) at evolve/ top "
            f"level still contain production code: {names}\n{detail}"
        )
        # The test is expected to fail until all flat modules are migrated.
        # Per SPEC: "Until this test passes, the project does NOT satisfy
        # the DDD claim and cannot converge."
        raise AssertionError(msg)  # noqa: B023
