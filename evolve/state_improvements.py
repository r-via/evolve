"""Backward-compat shim — improvement-parsing helpers.

Real implementation lives at
``evolve.infrastructure.filesystem.improvement_parser``.
This shim preserves all existing ``from evolve.state_improvements import X``
call sites (including ``evolve/state.py``'s re-export chain).
"""

from evolve.infrastructure.filesystem.improvement_parser import (  # noqa: F401
    _count_blocked,
    _count_checked,
    _count_unchecked,
    _detect_backlog_violation,
    _extract_unchecked_lines,
    _extract_unchecked_set,
    _get_current_improvement,
    _is_needs_package,
    _parse_check_output,
)
