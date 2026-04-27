"""Lock-in tests for the canonical ``evolve.sync_readme`` module path
(US-034, mirrors ``tests/test_memory_curation_module.py`` /
``tests/test_draft_review_module.py`` /
``tests/test_spec_archival_module.py`` /
``tests/test_oneshot_agents_module.py``).

The structural extraction in US-034 moves the three sync-readme symbols
(``build_sync_readme_prompt``, ``_run_sync_readme_claude_agent``,
``run_sync_readme_agent``) and the ``SYNC_README_NO_CHANGES_SENTINEL``
constant from ``evolve/oneshot_agents.py`` (which had grown to 812
lines, 1.6× the SPEC § "Hard rule: source files MUST NOT exceed 500
lines" cap) into the dedicated ``evolve/sync_readme.py`` leaf module.
The pre-existing sync-readme test files
(``tests/test_sync_readme.py``, ``tests/test_agent_sdk_coverage.py``)
only import the re-exports from ``evolve.agent``, so deleting the new
module would NOT make those tests fail — defeating the purpose of the
split.

This file:

(a) imports each public name **directly** from ``evolve.sync_readme``,
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
    "SYNC_README_NO_CHANGES_SENTINEL",
    "build_sync_readme_prompt",
    "_run_sync_readme_claude_agent",
    "run_sync_readme_agent",
)


def test_canonical_imports_resolve_from_evolve_sync_readme():
    """Every public name documented for the sync-readme agent must be
    importable from the new canonical path."""
    from evolve.sync_readme import (  # noqa: F401
        SYNC_README_NO_CHANGES_SENTINEL,
        build_sync_readme_prompt,
        _run_sync_readme_claude_agent,
        run_sync_readme_agent,
    )


def test_re_exports_are_is_identical_to_canonical_module():
    """``evolve.agent`` re-exports must point at the SAME objects bound
    in ``evolve.sync_readme`` — not duplicates, not shims.  The chain
    runs ``evolve.agent`` → ``evolve.oneshot_agents`` →
    ``evolve.sync_readme``; every link must preserve identity."""
    import evolve.agent as agent_mod
    import evolve.oneshot_agents as oneshot_mod
    import evolve.sync_readme as sync_readme_mod

    for name in _CANONICAL_NAMES:
        canonical = getattr(sync_readme_mod, name)
        oneshot_re_exported = getattr(oneshot_mod, name)
        agent_re_exported = getattr(agent_mod, name)
        assert canonical is oneshot_re_exported, (
            f"evolve.oneshot_agents.{name} must be the SAME object as "
            f"evolve.sync_readme.{name} (re-export, not duplicate)"
        )
        assert canonical is agent_re_exported, (
            f"evolve.agent.{name} must be the SAME object as "
            f"evolve.sync_readme.{name} (re-export, not duplicate)"
        )


def test_sync_readme_module_is_a_leaf():
    """The canonical module must NOT import from ``evolve.agent``,
    ``evolve.orchestrator``, ``evolve.cli``, or ``evolve.oneshot_agents``
    at module top level.

    Function-local (indented) imports are intentionally allowed — the
    builder looks up ``_load_project_context`` lazily, the
    ``_run_sync_readme_claude_agent`` runner looks up ``EFFORT`` /
    ``_patch_sdk_parser`` / ``get_tui`` lazily, and
    ``run_sync_readme_agent`` looks up ``_run_agent_with_retries`` and
    ``build_sync_readme_prompt`` lazily so that:

    1. tests that ``patch("evolve.agent.X")`` continue to intercept
       (memory.md round-7 lesson + round-2-of-20260427_200209 entry),
    2. ``EFFORT`` runtime mutation by ``_resolve_config`` keeps
       propagating into the SDK options, and
    3. module-load order remains acyclic (memory.md round-7 entry:
       indented imports don't trip the leaf-invariant regex
       ``^from evolve\\.``).
    """
    import evolve.sync_readme as sync_readme_mod

    src = Path(sync_readme_mod.__file__).read_text()
    leaf_violations = re.findall(
        r"^from evolve\.(agent|orchestrator|cli|oneshot_agents)( |$|\.)",
        src,
        re.MULTILINE,
    )
    assert leaf_violations == [], (
        "evolve/sync_readme.py must remain a leaf module — no "
        "top-level imports from evolve.{agent,orchestrator,cli,"
        "oneshot_agents}. "
        f"Found: {leaf_violations}"
    )


def test_sync_readme_under_500_line_cap():
    """``evolve/sync_readme.py`` must satisfy SPEC § "Hard rule: source
    files MUST NOT exceed 500 lines" — the very cap this extraction was
    designed to help enforce.  If the module ever crosses 500 lines a
    follow-up split is required, just like US-034 split off
    ``oneshot_agents.py``."""
    import evolve.sync_readme as sync_readme_mod

    src = Path(sync_readme_mod.__file__).read_text()
    line_count = src.count("\n") + (0 if src.endswith("\n") else 1)
    assert line_count <= 500, (
        f"evolve/sync_readme.py is {line_count} lines — exceeds the "
        "SPEC § 'Hard rule: source files MUST NOT exceed 500 lines' cap. "
        "Split into a leaf sub-module per the established pattern."
    )
