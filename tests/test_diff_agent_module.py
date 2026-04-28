"""Lock-in tests for the canonical ``evolve.diff_agent`` module path
(US-047, mirrors ``tests/test_sync_readme_module.py``,
``tests/test_oneshot_agents_module.py``,
``tests/test_memory_curation_module.py``,
``tests/test_draft_review_module.py``).

The structural extraction in US-047 moves the three diff-agent
symbols (``build_diff_prompt``, ``_run_diff_claude_agent``,
``run_diff_agent``) from ``evolve/oneshot_agents.py`` (which had
grown to 620 lines, 1.24× the SPEC § "Hard rule: source files
MUST NOT exceed 500 lines" cap) into the dedicated
``evolve/diff_agent.py`` leaf module.  The pre-existing diff-agent
test files (``tests/test_diff.py``, ``tests/test_agent_sdk_coverage.py``)
only import the re-exports from ``evolve.agent``, so deleting the
new module would NOT make those tests fail — defeating the purpose
of the split.

This file:

(a) imports each public name **directly** from ``evolve.diff_agent``,
(b) asserts the bound objects are ``is``-identical to the re-exports
    surfaced via ``evolve.agent`` (and through the chain to
    ``evolve.oneshot_agents``), and
(c) re-asserts the leaf-module invariant (no top-level
    ``from evolve.{agent,orchestrator,cli,oneshot_agents}`` imports).
"""

from __future__ import annotations

import re
from pathlib import Path


_CANONICAL_NAMES = (
    "build_diff_prompt",
    "_run_diff_claude_agent",
    "run_diff_agent",
)


def test_canonical_imports_resolve_from_evolve_diff_agent():
    """Every public name documented for the diff agent must be
    importable from the new canonical path."""
    from evolve.diff_agent import (  # noqa: F401
        build_diff_prompt,
        _run_diff_claude_agent,
        run_diff_agent,
    )


def test_re_exports_are_is_identical_to_canonical_module():
    """``evolve.agent`` re-exports must point at the SAME objects bound
    in ``evolve.diff_agent`` — not duplicates, not shims.  The chain
    runs ``evolve.agent`` → ``evolve.oneshot_agents`` →
    ``evolve.diff_agent``; every link must preserve identity."""
    import evolve.agent as agent_mod
    import evolve.oneshot_agents as oneshot_mod
    import evolve.diff_agent as diff_mod

    for name in _CANONICAL_NAMES:
        canonical = getattr(diff_mod, name)
        oneshot_re_exported = getattr(oneshot_mod, name)
        agent_re_exported = getattr(agent_mod, name)
        assert canonical is oneshot_re_exported, (
            f"evolve.oneshot_agents.{name} must be the SAME object as "
            f"evolve.diff_agent.{name} (re-export, not duplicate)"
        )
        assert canonical is agent_re_exported, (
            f"evolve.agent.{name} must be the SAME object as "
            f"evolve.diff_agent.{name} (re-export, not duplicate)"
        )


def test_diff_agent_module_is_a_leaf():
    """The canonical module must NOT import from ``evolve.agent``,
    ``evolve.orchestrator``, ``evolve.cli``, or ``evolve.oneshot_agents``
    at module top level.

    Function-local (indented) imports are intentionally allowed — the
    builder looks up ``_load_project_context`` lazily,
    ``_run_diff_claude_agent`` looks up ``_run_readonly_claude_agent``
    via ``evolve.oneshot_agents`` lazily, and ``run_diff_agent`` looks
    up ``_run_agent_with_retries`` lazily so that:

    1. tests that ``patch("evolve.agent.X")`` continue to intercept
       (memory.md round-7 lesson + round-2-of-20260427_200209 entry),
    2. the shared ``_run_readonly_claude_agent`` helper stays in
       ``evolve.oneshot_agents``, and
    3. module-load order remains acyclic (memory.md round-7 entry:
       indented imports don't trip the leaf-invariant regex
       ``^from evolve\\.``).
    """
    import evolve.diff_agent as diff_mod

    src = Path(diff_mod.__file__).read_text()
    leaf_violations = re.findall(
        r"^from evolve\.(agent|orchestrator|cli|oneshot_agents)( |$|\.)",
        src,
        re.MULTILINE,
    )
    assert leaf_violations == [], (
        "evolve/diff_agent.py must remain a leaf module — no "
        "top-level imports from evolve.{agent,orchestrator,cli,"
        "oneshot_agents}. "
        f"Found: {leaf_violations}"
    )


def test_diff_agent_under_500_line_cap():
    """``evolve/diff_agent.py`` must satisfy SPEC § "Hard rule: source
    files MUST NOT exceed 500 lines" — the very cap this extraction was
    designed to help enforce."""
    import evolve.diff_agent as diff_mod

    src = Path(diff_mod.__file__).read_text()
    line_count = src.count("\n") + (0 if src.endswith("\n") else 1)
    assert line_count <= 500, (
        f"evolve/diff_agent.py is {line_count} lines — exceeds the "
        "SPEC § 'Hard rule: source files MUST NOT exceed 500 lines' cap. "
        "Split into a leaf sub-module per the established pattern."
    )


def test_oneshot_agents_under_500_line_cap_after_extraction():
    """``evolve/oneshot_agents.py`` must drop below the 500-line cap
    after the US-047 extraction.  Mirrors the per-file split rule from
    memory.md Patterns § "Per-file split: include <500 line-count test
    on BOTH siblings — prevents silent cap re-crossing in follow-up
    rounds." """
    import evolve.oneshot_agents as oneshot_mod

    src = Path(oneshot_mod.__file__).read_text()
    line_count = src.count("\n") + (0 if src.endswith("\n") else 1)
    assert line_count <= 500, (
        f"evolve/oneshot_agents.py is {line_count} lines — extraction "
        "did not drop it under the SPEC § 'Hard rule' cap.  Re-check "
        "US-047 acceptance criterion 4."
    )
