"""Lock-in test for the canonical ``evolve.spec_archival`` module path
(addresses round-6 review HIGH-2).

The structural extraction in round 6 moved Sid (the SPEC archival agent)
from ``evolve/agent.py`` into the dedicated ``evolve/spec_archival.py``
module.  The pre-existing ``tests/test_spec_archival.py`` only imports
the re-exports from ``evolve.agent``, so deleting the new module would
NOT make those tests fail — defeating the purpose of the split.

This file imports each public name **directly** from
``evolve.spec_archival`` and asserts that the bound objects are
``is``-identical to the re-exports surfaced via ``evolve.agent``.  If
either the canonical module or the re-export drifts, the test fails.
"""

from __future__ import annotations


def test_canonical_imports_resolve_from_evolve_spec_archival():
    """Every public name documented for Sid must be importable from the
    new canonical path."""
    from evolve.spec_archival import (  # noqa: F401
        ARCHIVAL_LINE_THRESHOLD,
        ARCHIVAL_ROUND_INTERVAL,
        run_spec_archival,
        _should_run_spec_archival,
        build_spec_archival_prompt,
    )


def test_re_exports_are_is_identical_to_canonical_module():
    """``evolve.agent`` re-exports must point at the SAME objects bound
    in ``evolve.spec_archival`` — not duplicates, not shims."""
    import evolve.agent as agent_mod
    import evolve.spec_archival as archival_mod

    for name in (
        "ARCHIVAL_LINE_THRESHOLD",
        "ARCHIVAL_ROUND_INTERVAL",
        "run_spec_archival",
        "_should_run_spec_archival",
        "build_spec_archival_prompt",
    ):
        canonical = getattr(archival_mod, name)
        re_exported = getattr(agent_mod, name)
        assert canonical is re_exported, (
            f"evolve.agent.{name} must be the SAME object as "
            f"evolve.spec_archival.{name} (re-export, not duplicate)"
        )


def test_threshold_constants_have_expected_values():
    """Spec-pinned values per SPEC § 'SPEC archival (Sid)'."""
    from evolve.spec_archival import (
        ARCHIVAL_LINE_THRESHOLD,
        ARCHIVAL_ROUND_INTERVAL,
    )

    assert ARCHIVAL_LINE_THRESHOLD == 2000
    assert ARCHIVAL_ROUND_INTERVAL == 20
