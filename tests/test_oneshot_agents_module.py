"""Lock-in tests for the canonical ``evolve.oneshot_agents`` module path
(US-033, mirrors ``tests/test_memory_curation_module.py`` /
``tests/test_draft_review_module.py`` / ``tests/test_spec_archival_module.py``).

The structural extraction in US-033 moves the four one-shot agents
(dry-run, validate, diff, sync-readme) and their shared SDK runner
(``_run_readonly_claude_agent``) plus the shared check-section helper
(``_build_check_section``) and the sync-readme sentinel
(``SYNC_README_NO_CHANGES_SENTINEL``) from ``evolve/agent.py`` into the
dedicated ``evolve/oneshot_agents.py`` leaf module so ``agent.py`` drops
toward the SPEC § "Hard rule: source files MUST NOT exceed 500 lines"
cap.  The pre-existing one-shot test files (``tests/test_dry_run.py``,
``tests/test_validate.py``, ``tests/test_diff.py``,
``tests/test_sync_readme.py``) only import the re-exports from
``evolve.agent``, so deleting the new module would NOT make those tests
fail — defeating the purpose of the split.

This file:

(a) imports each public name **directly** from ``evolve.oneshot_agents``,
(b) asserts the bound objects are ``is``-identical to the re-exports
    surfaced via ``evolve.agent``, and
(c) re-asserts the leaf-module invariant (no top-level
    ``from evolve.{agent,orchestrator,cli}`` imports).
"""

from __future__ import annotations

import re
from pathlib import Path


_CANONICAL_NAMES = (
    "SYNC_README_NO_CHANGES_SENTINEL",
    "_build_check_section",
    "build_validate_prompt",
    "build_dry_run_prompt",
    "_run_readonly_claude_agent",
    "_run_dry_run_claude_agent",
    "run_dry_run_agent",
    "_run_validate_claude_agent",
    "run_validate_agent",
    "build_diff_prompt",
    "_run_diff_claude_agent",
    "run_diff_agent",
    "build_sync_readme_prompt",
    "_run_sync_readme_claude_agent",
    "run_sync_readme_agent",
)


def test_canonical_imports_resolve_from_evolve_oneshot_agents():
    """Every public name documented for the one-shot agents must be
    importable from the new canonical path."""
    from evolve.oneshot_agents import (  # noqa: F401
        SYNC_README_NO_CHANGES_SENTINEL,
        _build_check_section,
        build_validate_prompt,
        build_dry_run_prompt,
        _run_readonly_claude_agent,
        _run_dry_run_claude_agent,
        run_dry_run_agent,
        _run_validate_claude_agent,
        run_validate_agent,
        build_diff_prompt,
        _run_diff_claude_agent,
        run_diff_agent,
        build_sync_readme_prompt,
        _run_sync_readme_claude_agent,
        run_sync_readme_agent,
    )


def test_re_exports_are_is_identical_to_canonical_module():
    """``evolve.agent`` re-exports must point at the SAME objects bound
    in ``evolve.oneshot_agents`` — not duplicates, not shims."""
    import evolve.agent as agent_mod
    import evolve.oneshot_agents as oneshot_mod

    for name in _CANONICAL_NAMES:
        canonical = getattr(oneshot_mod, name)
        re_exported = getattr(agent_mod, name)
        assert canonical is re_exported, (
            f"evolve.agent.{name} must be the SAME object as "
            f"evolve.oneshot_agents.{name} (re-export, not duplicate)"
        )


def test_oneshot_agents_module_is_a_leaf():
    """The canonical module must NOT import from ``evolve.agent``,
    ``evolve.orchestrator``, or ``evolve.cli`` at module top level.

    Function-local (indented) imports are intentionally allowed —
    the build_* helpers look up ``_load_project_context`` lazily, the
    ``_run_*_claude_agent`` runners look up ``EFFORT`` /
    ``_patch_sdk_parser`` lazily, and the ``run_*_agent`` wrappers
    look up ``_run_agent_with_retries`` lazily so that:

    1. tests that ``patch("evolve.agent.X")`` continue to intercept
       (memory.md round-7 lesson),
    2. ``EFFORT`` runtime mutation by ``_resolve_config`` keeps
       propagating into the SDK options, and
    3. module-load order remains acyclic (memory.md round-7 entry:
       indented imports don't trip the leaf-invariant regex
       ``^from evolve\\.``).
    """
    import evolve.oneshot_agents as oneshot_mod

    src = Path(oneshot_mod.__file__).read_text()
    leaf_violations = re.findall(
        r"^from evolve\.(agent|orchestrator|cli)( |$|\.)",
        src,
        re.MULTILINE,
    )
    assert leaf_violations == [], (
        "evolve/oneshot_agents.py must remain a leaf module — no "
        "top-level imports from evolve.{agent,orchestrator,cli}. "
        f"Found: {leaf_violations}"
    )
