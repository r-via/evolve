"""Tests for US-075: memory_curation.py → infrastructure/claude_sdk/memory_curation.py migration."""

from pathlib import Path

import evolve.memory_curation as shim_mod
import evolve.infrastructure.claude_sdk.memory_curation as infra_mod
import evolve.agent as agent_mod


# Symbols that must be present in the infrastructure module and re-exported
_SYMBOLS = [
    "_should_run_curation",
    "build_memory_curation_prompt",
    "_run_memory_curation_claude_agent",
    "run_memory_curation",
]


class TestMemoryCurationMigration:
    """Verify the DDD migration preserves identity and the leaf invariant."""

    def test_symbols_importable_from_infrastructure(self):
        """AC 1: all 4 symbols importable from evolve.infrastructure.claude_sdk.memory_curation."""
        for name in _SYMBOLS:
            assert hasattr(infra_mod, name), f"{name} missing from infrastructure module"

    def test_identity_shim_to_infrastructure(self):
        """AC 3: shim re-exports are identity-equal to infrastructure originals."""
        for name in _SYMBOLS:
            assert getattr(shim_mod, name) is getattr(infra_mod, name), (
                f"evolve.memory_curation.{name} is not the same object as "
                f"evolve.infrastructure.claude_sdk.memory_curation.{name}"
            )

    def test_identity_agent_to_infrastructure(self):
        """AC 3: agent.py re-exports are identity-equal to infrastructure originals."""
        for name in _SYMBOLS:
            assert getattr(agent_mod, name) is getattr(infra_mod, name), (
                f"evolve.agent.{name} is not the same object as "
                f"evolve.infrastructure.claude_sdk.memory_curation.{name}"
            )

    def test_no_top_level_evolve_dot_imports(self):
        """AC 4: infrastructure module has no top-level ``from evolve.<legacy>`` imports."""
        src = Path(infra_mod.__file__).read_text()
        import ast
        tree = ast.parse(src)
        violations = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith("evolve.") and node.module not in (
                    "evolve.infrastructure.claude_sdk.runtime",
                    "evolve.infrastructure.claude_sdk",
                    "evolve.infrastructure.filesystem",
                ):
                    violations.append(node.module)
        assert not violations, (
            f"Top-level evolve.* imports found in infrastructure module: {violations}"
        )

    def test_constants_importable(self):
        """Additional symbols (constants) are importable and consistent."""
        assert infra_mod.CURATION_LINE_THRESHOLD == shim_mod.CURATION_LINE_THRESHOLD
        assert infra_mod.CURATION_ROUND_INTERVAL == shim_mod.CURATION_ROUND_INTERVAL
        assert infra_mod._CURATION_MAX_SHRINK == shim_mod._CURATION_MAX_SHRINK

    def test_infrastructure_file_under_500_lines(self):
        """Infrastructure module is under the 500-line cap."""
        src = Path(infra_mod.__file__).read_text()
        line_count = len(src.splitlines())
        assert line_count <= 500, (
            f"evolve/infrastructure/claude_sdk/memory_curation.py is {line_count} lines (cap: 500)"
        )

    def test_shim_file_under_500_lines(self):
        """Shim module is under the 500-line cap."""
        src = Path(shim_mod.__file__).read_text()
        line_count = len(src.splitlines())
        assert line_count <= 500, (
            f"evolve/memory_curation.py is {line_count} lines (cap: 500)"
        )
