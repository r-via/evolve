"""US-045: tests/test_cli_config_module.py — regression tests locking the
extraction of CLI config-resolution helpers (``EFFORT_LEVELS``,
``_validate_effort``, ``_load_config``, ``_resolve_config``) from
``evolve/cli.py`` into ``evolve/cli_config.py``.

Per AC5:
  (a) each of the four symbols is importable from ``evolve.cli_config``
  (b) ``is``-equality holds between ``evolve.cli.X`` and
      ``evolve.cli_config.X`` for each symbol (re-export identity check)
  (c) ``evolve/cli_config.py`` source contains no top-level
      ``from evolve.agent``, ``from evolve.orchestrator``, or
      ``from evolve.cli`` imports (leaf-module invariant)

Mirrors the proven re-export pattern from US-039 (cli_utils), US-030
(agent_runtime), US-031 (memory_curation), US-032 (draft_review),
US-034 (sync_readme), and US-044 (state_improvements).
"""

import re
from pathlib import Path

import pytest

import evolve.cli as cli_mod
import evolve.cli_config as cli_config_mod


_CLI_CONFIG_SYMBOLS = (
    "EFFORT_LEVELS",
    "_validate_effort",
    "_load_config",
    "_resolve_config",
)


@pytest.mark.parametrize("name", _CLI_CONFIG_SYMBOLS)
def test_cli_config_module_exposes_symbol(name: str) -> None:
    """AC5(a): each symbol is importable from ``evolve.cli_config``."""
    assert hasattr(cli_config_mod, name), (
        f"evolve.cli_config missing extracted symbol {name!r}"
    )


@pytest.mark.parametrize("name", _CLI_CONFIG_SYMBOLS)
def test_cli_module_reexports_same_object(name: str) -> None:
    """AC5(b): ``evolve.cli.X is evolve.cli_config.X`` (re-export identity).

    The re-export at the top of ``evolve/cli.py`` MUST bind the same
    object so ``patch("evolve.cli._resolve_config", ...)`` and
    ``from evolve.cli import _resolve_config`` keep working — same lesson
    as US-028's ``_diag.`` de-aliasing and the chain of split US items
    that followed.
    """
    src = getattr(cli_config_mod, name)
    reexp = getattr(cli_mod, name)
    assert src is reexp, (
        f"evolve.cli.{name} is not the same object as evolve.cli_config.{name} "
        "— re-export chain is broken; tests that patch evolve.cli will not "
        "intercept the bound name."
    )


def test_cli_config_is_a_leaf_module() -> None:
    """AC5(c): ``evolve/cli_config.py`` has zero top-level
    ``from evolve.{agent,orchestrator,cli}`` imports.

    Indented (function-local) imports are exempt — they do not trip the
    leaf-invariant regex.  This mirrors the leaf-module check from
    US-039's ``test_cli_utils_module.py`` and the agent.py / orchestrator.py
    extraction-test family.
    """
    src_path = Path(__file__).resolve().parent.parent / "evolve" / "cli_config.py"
    src = src_path.read_text()
    forbidden = re.compile(r"^from evolve\.(agent|orchestrator|cli)( |$|\.)", re.MULTILINE)
    matches = forbidden.findall(src)
    assert not matches, (
        f"evolve/cli_config.py has forbidden top-level imports: {matches!r}. "
        "Move them inside function bodies to satisfy the leaf-module invariant."
    )


def test_cli_config_under_500_line_cap() -> None:
    """Belt-and-suspenders: lock the new module under SPEC.md § 'Hard
    rule: source files MUST NOT exceed 500 lines'.

    Memory.md round-3-of-20260427_203955 lesson: every per-file split
    must include a ``< 500`` line-count test on the new sibling — prevents
    silent cap re-crossing on future edits.
    """
    src_path = Path(__file__).resolve().parent.parent / "evolve" / "cli_config.py"
    line_count = len(src_path.read_text().splitlines())
    assert line_count <= 500, (
        f"evolve/cli_config.py is {line_count} lines — exceeds the 500-line "
        "SPEC.md hard cap.  Split before re-committing."
    )


def test_cli_under_500_line_cap_after_extract() -> None:
    """Locks the AC4 contract: ``evolve/cli.py`` is under the 500-line
    SPEC cap after the US-045 extraction.

    Memory.md round-3-of-20260427_203955 lesson applies to BOTH the new
    file AND the source — without this test the cap could silently
    re-cross via future cli.py additions.
    """
    src_path = Path(__file__).resolve().parent.parent / "evolve" / "cli.py"
    line_count = len(src_path.read_text().splitlines())
    assert line_count <= 500, (
        f"evolve/cli.py is {line_count} lines — exceeds the 500-line SPEC.md "
        "hard cap.  Extract another coherent chunk before re-committing."
    )


def test_validate_effort_accepts_documented_values() -> None:
    """Behavioral lock: ``_validate_effort`` accepts the four documented
    effort levels and rejects everything else (matches SPEC § 'The --effort
    flag').
    """
    for level in ("low", "medium", "high", "max"):
        assert cli_config_mod._validate_effort(level) == level

    import argparse

    with pytest.raises(argparse.ArgumentTypeError):
        cli_config_mod._validate_effort("ultra")


def test_effort_levels_constant_value() -> None:
    """``EFFORT_LEVELS`` is the canonical four-tuple per SPEC.md § 'The
    --effort flag'.  Drift here breaks every callsite that imports the
    constant.
    """
    assert cli_config_mod.EFFORT_LEVELS == ("low", "medium", "high", "max")
