"""Spec-anchored runtime constants for the orchestrator.

Extracted from ``evolve/orchestrator.py`` so that file stays under the
SPEC § "Hard rule: source files MUST NOT exceed 500 lines" cap.
``orchestrator.py`` re-exports every name defined here, preserving
``from evolve.orchestrator import MAX_DEBUG_RETRIES`` (etc.) imports
across the test suite and the codebase.

This is a leaf module — stdlib-only — so it can be imported from any
sibling without risking a cycle.
"""

from __future__ import annotations


# Maximum number of debug retries when a round fails, stalls, or makes
# no progress.
MAX_DEBUG_RETRIES = 2


# Memory-wipe sanity gate constants — keep the runtime check aligned
# with SPEC.md § "memory.md" — "Byte-size sanity gate".  Changing
# either value here is the single source of truth for both the
# detection logic in ``evolve.round_lifecycle`` and the tests that
# exercise it.
#
#   _MEMORY_COMPACTION_MARKER — the literal string the agent must
#       include in its commit message (on its own line, per SPEC) to
#       legitimise a large memory.md shrink.  Absence of the marker
#       on a >threshold shrink triggers a debug retry with the
#       "silently wiped memory.md" diagnostic header.
#   _MEMORY_WIPE_THRESHOLD   — fractional shrink floor below which
#       memory.md is considered wiped.  0.5 means "memory.md after
#       the round is smaller than half of its pre-round size" → retry.
_MEMORY_COMPACTION_MARKER = "memory: compaction"
_MEMORY_WIPE_THRESHOLD = 0.5


# Backlog discipline rule 1 (empty-queue gate) constants — keep the
# runtime check aligned with SPEC.md § "Backlog discipline".  The
# agent is forbidden from adding a new ``- [ ]`` item while any other
# ``- [ ]`` item already exists in improvements.md.  When detected,
# the orchestrator triggers a debug retry whose diagnostic prefix
# carries the documented header so agent.py's prompt builder can
# render the dedicated section.
_BACKLOG_VIOLATION_PREFIX = "BACKLOG VIOLATION"
_BACKLOG_VIOLATION_HEADER = (
    "CRITICAL \u2014 Backlog discipline violation: "
    "new item added while queue non-empty"
)
