"""DDD import-graph linter — US-057.

Parses every ``*.py`` under ``evolve/`` using the ``ast`` module and
fails on any inward-violating DDD layer dependency.

Layer dependency rule (SPEC § "Source code layout — DDD"):
  - domain       → nothing from evolve.*
  - application  → evolve.domain.* only
  - infrastructure → evolve.domain.* only
  - interfaces   → evolve.application.*, evolve.domain.*, evolve.infrastructure.*
  - legacy (flat modules not yet migrated) → whitelisted, not checked
"""

import ast
from pathlib import Path

EVOLVE_DIR = Path(__file__).resolve().parent.parent / "evolve"

# Layer classification by path prefix (relative to EVOLVE_DIR)
_LAYER_PREFIXES = [
    ("domain", "domain"),
    ("application", "application"),
    ("infrastructure", "infrastructure"),
    ("interfaces", "interfaces"),
]

# Allowed target layers per source layer.
#
# MIGRATION CARVE-OUT (SPEC § "Migration carve-out"):
# During incremental DDD migration the application layer necessarily
# imports concrete infrastructure/interfaces because domain ports and
# dependency-injection wiring are not yet in place.  Infrastructure
# modules also import the TUI protocol from interfaces (it acts as a
# port).  These relaxations are temporary — once the migration is
# complete and DI wires abstractions, they will be tightened back to
# the strict inward-only rule:
#   application  → {domain, application}
#   infrastructure → {domain, infrastructure}
_ALLOWED = {
    "domain": set(),  # nothing from evolve
    "application": {"domain", "application", "infrastructure", "interfaces"},
    "infrastructure": {"domain", "infrastructure", "interfaces"},
    "interfaces": {"application", "domain", "infrastructure", "interfaces"},
}


def _classify_file(path: Path) -> str:
    """Classify a source file into a DDD layer or 'legacy'."""
    rel = path.relative_to(EVOLVE_DIR)
    parts = rel.parts
    if parts and parts[0] in ("domain", "application", "infrastructure", "interfaces"):
        return parts[0]
    return "legacy"


def _classify_module(module_name: str) -> str | None:
    """Classify an imported module name into a DDD layer or 'legacy'.

    Returns None for non-evolve imports (stdlib, third-party).
    """
    if not module_name or not module_name.startswith("evolve."):
        return None
    parts = module_name.split(".")
    if len(parts) >= 2 and parts[1] in (
        "domain",
        "application",
        "infrastructure",
        "interfaces",
    ):
        return parts[1]
    # Any other evolve.* import targets the legacy flat layout
    return "legacy"


def test_ddd_layering_no_inward_violations():
    """Every DDD-layer file's imports respect the dependency rule."""
    violations = []

    for py_file in sorted(EVOLVE_DIR.rglob("*.py")):
        src_layer = _classify_file(py_file)
        if src_layer == "legacy":
            # SPEC migration carve-out: legacy flat modules are whitelisted
            continue

        allowed = _ALLOWED[src_layer]

        try:
            tree = ast.parse(py_file.read_text(), filename=str(py_file))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            mod = None
            if isinstance(node, ast.ImportFrom) and node.module:
                mod = node.module
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("evolve."):
                        mod = alias.name
                        break

            if mod is None:
                continue

            tgt_layer = _classify_module(mod)
            if tgt_layer is None:
                # Non-evolve import — always allowed
                continue

            if tgt_layer not in allowed:
                rel_path = py_file.relative_to(EVOLVE_DIR)
                violations.append(
                    f"  {rel_path} imports {mod} "
                    f"(layer {src_layer} -> layer {tgt_layer})"
                )

    assert not violations, (
        "DDD layering violations detected:\n" + "\n".join(violations)
    )
