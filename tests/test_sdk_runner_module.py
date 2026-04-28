"""Re-export identity + leaf-module invariant for ``evolve/sdk_runner.py``.

The round-7 ``sdk_runner`` extraction (agent.py split step 6) hoisted
``_build_multimodal_prompt`` and ``run_claude_agent`` out of
``evolve/agent.py`` into the leaf module ``evolve/sdk_runner.py`` per
SPEC § "Hard rule: source files MUST NOT exceed 500 lines" — agent.py
was 601 lines (1.20× the cap) before the extraction.

These tests lock the same contract proven by US-027 / US-030 / US-031 /
US-032 / US-033 / US-034 / US-035 module tests:

1. Each hoisted symbol is importable from ``evolve.sdk_runner``.
2. ``is``-equality holds between ``evolve.agent.X`` and
   ``evolve.sdk_runner.X`` (re-export identity — patches against
   ``evolve.agent.run_claude_agent`` continue to intercept the
   bound name in the ``analyze_and_fix`` call site).
3. ``evolve/sdk_runner.py`` source contains no top-level
   ``from evolve.agent``, ``from evolve.orchestrator``, or
   ``from evolve.cli`` imports (leaf-module invariant — the
   ``EFFORT`` lookup is lazy-imported at function scope so the
   regex doesn't trip).
4. Both files (``agent.py`` and ``sdk_runner.py``) stay under the
   500-line cap (memory.md "Per-file split: include <500 line-count
   test on BOTH siblings").
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import evolve.agent as agent_mod
import evolve.sdk_runner as sdk_runner_mod


HOISTED_SYMBOLS = (
    "_build_multimodal_prompt",
    "run_claude_agent",
)


@pytest.mark.parametrize("name", HOISTED_SYMBOLS)
def test_symbol_importable_from_sdk_runner(name):
    """Every hoisted symbol must be importable from ``evolve.sdk_runner``."""
    assert hasattr(sdk_runner_mod, name), (
        f"evolve.sdk_runner must define {name} per the round-7 extraction "
        "(agent.py split step 6)"
    )


@pytest.mark.parametrize("name", HOISTED_SYMBOLS)
def test_reexport_identity_with_agent(name):
    """``is``-equality between ``evolve.agent.X`` and
    ``evolve.sdk_runner.X`` — proves the re-export at agent.py top
    binds the same object so existing test patch targets like
    ``patch("evolve.agent.run_claude_agent", ...)`` continue to
    intercept ``analyze_and_fix``'s internal call (same lesson as
    US-028's ``_diag.`` de-aliasing).
    """
    assert getattr(agent_mod, name) is getattr(sdk_runner_mod, name), (
        f"evolve.agent.{name} must be the same object as "
        f"evolve.sdk_runner.{name} (re-export identity check). "
        "If this fails, the agent.py re-export block has drifted from "
        "the sdk_runner definitions."
    )


def test_sdk_runner_is_a_leaf_module():
    """``evolve/sdk_runner.py`` must NOT import from ``evolve.agent``,
    ``evolve.orchestrator``, or ``evolve.cli`` at module top.

    Function-local imports inside ``run_claude_agent`` (notably
    ``from evolve.agent import EFFORT`` to honor the runtime mutation
    pattern from memory.md "--effort plumbing: 3-attempt pattern")
    are indented and do NOT trip this regex per memory.md round-7
    lesson "indented imports don't trip the leaf-invariant regex
    `^from evolve\\.`".
    """
    src = Path(sdk_runner_mod.__file__).read_text()
    forbidden = re.compile(r"^from evolve\.(agent|orchestrator|cli)( |$|\.)", re.MULTILINE)
    matches = forbidden.findall(src)
    assert not matches, (
        f"evolve/sdk_runner.py must be a leaf module (no top-level "
        f"imports from evolve.agent / evolve.orchestrator / evolve.cli). "
        f"Found: {matches}"
    )


def test_both_files_under_500_line_cap():
    """SPEC § 'Hard rule: source files MUST NOT exceed 500 lines'.

    The whole point of the round-7 extraction was to move agent.py
    under the cap.  Lock both siblings (agent.py + sdk_runner.py)
    so a follow-up round can't silently re-cross the cap by adding
    code to either file.
    """
    for path in (Path(agent_mod.__file__), Path(sdk_runner_mod.__file__)):
        n = len(path.read_text().splitlines())
        assert n <= 500, f"{path.name} has {n} lines, exceeds 500-line cap"
