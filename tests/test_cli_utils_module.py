"""US-039: tests/test_cli_utils_module.py — regression tests locking the
extraction of CLI utility subcommands (``_clean_sessions``,
``_show_history``, ``_show_status``) from ``evolve/cli.py`` into
``evolve/cli_utils.py``.

Per AC5:
  (a) each of the three symbols is importable from ``evolve.cli_utils``
  (b) ``is``-equality holds between ``evolve.cli.X`` and
      ``evolve.cli_utils.X`` for each symbol (re-export identity check)
  (c) ``evolve/cli_utils.py`` source contains no top-level
      ``from evolve.agent``, ``from evolve.orchestrator``, or
      ``from evolve.cli`` imports (leaf-module invariant)
"""

import re
from pathlib import Path

import pytest

import evolve.cli as cli_mod
import evolve.cli_utils as cli_utils_mod


_CLI_UTILS_SYMBOLS = ("_clean_sessions", "_show_history", "_show_status")


@pytest.mark.parametrize("name", _CLI_UTILS_SYMBOLS)
def test_cli_utils_module_exposes_symbol(name: str) -> None:
    """AC5(a): each symbol is importable from ``evolve.cli_utils``."""
    assert hasattr(cli_utils_mod, name), (
        f"evolve.cli_utils missing extracted symbol {name!r}"
    )
    assert callable(getattr(cli_utils_mod, name))


@pytest.mark.parametrize("name", _CLI_UTILS_SYMBOLS)
def test_cli_module_reexports_same_object(name: str) -> None:
    """AC5(b): ``evolve.cli.X is evolve.cli_utils.X`` (re-export identity).

    The re-export at the top of ``evolve/cli.py`` MUST bind the same
    object so ``patch("evolve.cli.X", ...)`` and ``from evolve.cli
    import X`` keep working — same lesson as US-028's ``_diag.``
    de-aliasing and US-030 / US-031 / US-032 extraction patterns.
    """
    src = getattr(cli_utils_mod, name)
    reexp = getattr(cli_mod, name)
    assert src is reexp, (
        f"evolve.cli.{name} is not the same object as evolve.cli_utils.{name} — "
        "re-export chain is broken; tests that patch evolve.cli will not "
        "intercept the bound name."
    )


def test_cli_utils_is_a_leaf_module() -> None:
    """AC5(c): ``evolve/cli_utils.py`` has zero top-level
    ``from evolve.{agent,orchestrator,cli}`` imports.

    Indented (function-local) imports are exempt — they do not trip the
    leaf-invariant regex.  This mirrors the leaf-module check from
    US-030's ``test_agent_runtime.py``, US-031's
    ``test_memory_curation_module.py``, and US-032's
    ``test_draft_review_module.py``.
    """
    src = (Path(__file__).resolve().parent.parent / "evolve" / "cli_utils.py").read_text()
    forbidden = re.compile(r"^from evolve\.(agent|orchestrator|cli)( |$|\.)", re.MULTILINE)
    matches = forbidden.findall(src)
    assert not matches, (
        f"evolve/cli_utils.py has forbidden top-level imports: {matches!r}. "
        "Move them inside function bodies to satisfy the leaf-module invariant."
    )


def test_cli_utils_under_500_line_cap() -> None:
    """Belt-and-suspenders: lock the new module under SPEC.md § 'Hard
    rule: source files MUST NOT exceed 500 lines'.

    Memory.md round-3-of-20260427_203955 lesson: every per-file split
    must include a ``< 500`` line-count test on the new sibling — prevents
    silent cap re-crossing on future edits.
    """
    src_path = Path(__file__).resolve().parent.parent / "evolve" / "cli_utils.py"
    line_count = len(src_path.read_text().splitlines())
    assert line_count <= 500, (
        f"evolve/cli_utils.py is {line_count} lines — exceeds the 500-line "
        "SPEC.md hard cap.  Split before re-committing."
    )
