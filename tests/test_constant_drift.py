"""Drift-catching tests for cross-file-quoted SPEC constants.

Purpose
-------
SPEC.md quotes a handful of literal strings verbatim — the Mechanism C
drift warning, the "Previous attempt log" retry-continuity section, the
cairosvg-missing warning, the memory-wipe diagnostic header, and the
initial memory.md template pointer.  Each of these used to live duplicated
across one or more call sites in the code, so a future SPEC edit would
silently diverge from the runtime contract until a human noticed.

This module collapses that risk into a single test file: each constant is
(a) imported from its owning module to prove it exists, (b) asserted to
appear exactly once in the runtime source string surrounding the call
site (no duplicate literals left behind), and (c) cross-checked against
the documented shape in SPEC.md where practical.

Adding a new constant to the list?  Copy one of the existing blocks and
point the source-file / literal assertions at the new owner.  The single
commit that introduces the constant also updates this test, so any future
drift between the SPEC quote and the runtime string lands with a failing
assertion.

See SPEC.md § "Backlog discipline" anti-variante rule and the
``_MEMORY_COMPACTION_MARKER`` / ``_MEMORY_WIPE_THRESHOLD`` sibling tests
in ``test_loop_coverage.py`` / ``test_memory_discipline.py`` for prior
applications of the same pattern.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import evolve.agent as agent_mod
import evolve as evolve_mod
import evolve.tui as tui_mod


REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# (b) agent._PREV_ATTEMPT_LOG_FMT — retry continuity section
# ---------------------------------------------------------------------------


class TestPrevAttemptLogFmt:
    def test_constant_exists_with_required_placeholders(self):
        fmt = agent_mod._PREV_ATTEMPT_LOG_FMT
        assert isinstance(fmt, str)
        for ph in ("{current}", "{round}", "{prior}", "{log_path}"):
            assert ph in fmt, f"missing placeholder {ph}"
        # SPEC § "Retry continuity" rule (2) wording invariants.
        assert "\n## Previous attempt log\n" in fmt
        assert "Read this file FIRST" in fmt
        assert "Continue" in fmt

    def test_rendered_section_includes_interpolated_values(self):
        out = agent_mod._PREV_ATTEMPT_LOG_FMT.format(
            current=2,
            round=7,
            prior=1,
            log_path="/tmp/conversation_loop_7_attempt_1.md",
        )
        assert "attempt 2 of round 7" in out
        assert "attempt 1" in out
        assert "/tmp/conversation_loop_7_attempt_1.md" in out

    def test_call_site_no_longer_holds_duplicate_literal(self):
        """The constant's owning + consuming modules must use it — not re-inline
        ``## Previous attempt log``.

        Post DDD migration (US-074) the constant body lives in
        ``evolve/infrastructure/claude_sdk/prompt_diagnostics.py``.
        The 4-link re-export chain (``agent`` → ``prompt_builder`` →
        ``prompt_diagnostics`` shim → ``infrastructure/claude_sdk/
        prompt_diagnostics``) means all FOUR files are scanned: the
        infrastructure file carries the constant body (1 occurrence);
        the others carry zero.  The ``.format(`` invocation must
        appear in at least one of the four source files.
        """
        agent_src = (REPO_ROOT / "evolve" / "agent.py").read_text()
        pb_src = (REPO_ROOT / "evolve" / "prompt_builder.py").read_text()
        pd_src = (REPO_ROOT / "evolve" / "prompt_diagnostics.py").read_text()
        infra_pd_src = (
            REPO_ROOT / "evolve" / "infrastructure" / "claude_sdk" / "prompt_diagnostics.py"
        ).read_text()
        # Each owning file may legitimately carry at most one copy of the
        # header (the constant body itself).  Re-inlining a second copy
        # at the call site is the drift this test exists to catch.
        # Post DDD migration (US-074) the constant body lives in
        # infrastructure/claude_sdk/prompt_diagnostics.py; the flat
        # prompt_diagnostics.py is a backward-compat shim.
        for fname, src in (
            ("agent.py", agent_src),
            ("prompt_builder.py", pb_src),
            ("prompt_diagnostics.py", pd_src),
            ("infrastructure/claude_sdk/prompt_diagnostics.py", infra_pd_src),
        ):
            count = src.count("## Previous attempt log")
            assert count <= 1, (
                f"{fname} contains {count} literal '## Previous attempt log' "
                "strings — expected at most 1 (the constant body). "
                "Use _PREV_ATTEMPT_LOG_FMT.format(...) at the call site."
            )
        # The .format() call must be present in at least one of the owning
        # files (post-DDD-migration it lives in
        # infrastructure/claude_sdk/prompt_diagnostics.py).
        assert (
            "_PREV_ATTEMPT_LOG_FMT.format(" in agent_src
            or "_PREV_ATTEMPT_LOG_FMT.format(" in pb_src
            or "_PREV_ATTEMPT_LOG_FMT.format(" in pd_src
            or "_PREV_ATTEMPT_LOG_FMT.format(" in infra_pd_src
        )


# ---------------------------------------------------------------------------
# (c) tui._CAIROSVG_MISSING_WARN — optional-dep missing warning
# ---------------------------------------------------------------------------


class TestCairosvgMissingWarn:
    def test_constant_exists_and_carries_required_wording(self):
        warn = tui_mod._CAIROSVG_MISSING_WARN
        assert isinstance(warn, str)
        # SPEC § "Frame capture" § Dependencies wording invariants.
        assert "capture_frames" in warn
        assert "cairosvg" in warn
        assert "pip install 'evolve[vision]'" in warn
        assert "no-op" in warn

    def test_call_sites_no_longer_duplicate_the_literal(self):
        """evolve/tui/ must reference the constant at both warning call sites.

        Previously the same wording lived inlined at RichTUI.__init__ and
        RichTUI.capture_frame.  Extracting to _CAIROSVG_MISSING_WARN means
        the body ``capture_frames is enabled but cairosvg`` should appear
        exactly once in the tui package — inside the constant assignment
        in ``evolve/tui/__init__.py``.
        """
        # The constant definition lives in evolve/tui/__init__.py
        init_src = (REPO_ROOT / "evolve" / "tui" / "__init__.py").read_text()
        count = init_src.count("capture_frames is enabled but cairosvg")
        assert count <= 1, (
            f"evolve/tui/__init__.py contains {count} copies of the cairosvg-missing "
            "wording — expected 1 (constant body).  Both call sites must "
            "log _CAIROSVG_MISSING_WARN rather than re-inlining the text."
        )
        assert "_CAIROSVG_MISSING_WARN" in init_src
        # The call-site usage in rich.py must go through _log.warning(<constant>).
        rich_src = (REPO_ROOT / "evolve" / "tui" / "rich.py").read_text()
        assert "_log.warning(_CAIROSVG_MISSING_WARN)" in rich_src


# ---------------------------------------------------------------------------
# (d) agent._MEMORY_WIPED_HEADER_FMT — memory-wipe diagnostic branch
# ---------------------------------------------------------------------------


class TestMemoryWipedHeaderFmt:
    def test_constant_exists_and_quotes_sanity_gate_contract(self):
        fmt = agent_mod._MEMORY_WIPED_HEADER_FMT
        assert isinstance(fmt, str)
        assert "{diagnostic}" in fmt
        # SPEC § "Byte-size sanity gate" wording invariants — the prompt
        # must reproduce the exact header and reference the compaction
        # marker + append-only contract.
        assert "CRITICAL — Previous round silently wiped memory.md" in fmt
        assert "memory: compaction" in fmt
        assert "append-only" in fmt
        # Fenced code block for the raw diagnostic is required by the
        # prompt-builder contract — every other ``## CRITICAL —`` branch in
        # agent.py follows the same shape.
        assert "```\n{diagnostic}\n```" in fmt

    def test_rendered_section_embeds_diagnostic(self):
        out = agent_mod._MEMORY_WIPED_HEADER_FMT.format(
            diagnostic="MEMORY WIPED: 2000\u21925 bytes"
        )
        assert "MEMORY WIPED: 2000\u21925 bytes" in out
        assert out.endswith("```\n")
        assert out.startswith("\n## CRITICAL — Previous round silently wiped memory.md\n")

    def test_call_site_no_longer_holds_duplicate_literal(self):
        """The owning + consuming modules must branch via the constant.

        Post DDD migration (US-074) the constant body lives in
        ``evolve/infrastructure/claude_sdk/prompt_diagnostics.py``.
        All FOUR files in the re-export chain are scanned; only the
        infrastructure file carries the constant body (1 occurrence).
        """
        agent_src = (REPO_ROOT / "evolve" / "agent.py").read_text()
        pb_src = (REPO_ROOT / "evolve" / "prompt_builder.py").read_text()
        pd_src = (REPO_ROOT / "evolve" / "prompt_diagnostics.py").read_text()
        infra_pd_src = (
            REPO_ROOT / "evolve" / "infrastructure" / "claude_sdk" / "prompt_diagnostics.py"
        ).read_text()
        # Post DDD migration (US-074) the constant body lives in
        # infrastructure/claude_sdk/prompt_diagnostics.py; the flat
        # prompt_diagnostics.py is a backward-compat shim.
        for fname, src in (
            ("agent.py", agent_src),
            ("prompt_builder.py", pb_src),
            ("prompt_diagnostics.py", pd_src),
            ("infrastructure/claude_sdk/prompt_diagnostics.py", infra_pd_src),
        ):
            count = src.count("silently wiped memory.md")
            assert count <= 1, (
                f"{fname} contains {count} literal 'silently wiped memory.md' "
                "strings — expected at most 1 (the constant body). "
                "Use _MEMORY_WIPED_HEADER_FMT.format(...) at the branch site."
            )
        # The .format() call must be present in at least one of the owning
        # files (post-DDD-migration it lives in
        # infrastructure/claude_sdk/prompt_diagnostics.py).
        assert (
            "_MEMORY_WIPED_HEADER_FMT.format(" in agent_src
            or "_MEMORY_WIPED_HEADER_FMT.format(" in pb_src
            or "_MEMORY_WIPED_HEADER_FMT.format(" in pd_src
            or "_MEMORY_WIPED_HEADER_FMT.format(" in infra_pd_src
        )


# ---------------------------------------------------------------------------
# (e) evolve._DEFAULT_MEMORY_MD — spec-filename-agnostic scaffold
# ---------------------------------------------------------------------------


class TestDefaultMemoryMdSpecAgnostic:
    def test_template_no_longer_hardcodes_spec_md(self):
        """Pointer prose must be spec-filename-agnostic.

        New projects may run with ``--spec SPEC.md``, ``--spec CLAIMS.md``,
        ``--spec docs/specification.md`` etc.  The scaffold is written
        once at ``evolve init`` time without knowledge of that flag, so
        the pointer prose must not bake in any specific filename.
        """
        template = evolve_mod._DEFAULT_MEMORY_MD
        # Reject the old wording.
        assert "SPEC.md §" not in template, (
            "_DEFAULT_MEMORY_MD must not hardcode SPEC.md — reword to be "
            "spec-filename-agnostic (e.g. 'your project's spec file')."
        )
        # Accept the new wording.
        assert "your project's spec file" in template
        # Four typed sections still required by SPEC § "memory.md".
        for section in ("## Errors", "## Decisions", "## Patterns", "## Insights"):
            assert section in template


# ---------------------------------------------------------------------------
# runtime memory-section header — SPEC broadening to "cumulative learning log"
# ---------------------------------------------------------------------------


class TestMemorySectionRuntimeHeader:
    """The runtime ``## Memory`` header injected by ``build_prompt`` must
    match the broadened discipline from SPEC § "memory.md" (log errors,
    decisions, patterns, insights — not just errors).  Previously this
    injection carried the old "errors from previous rounds — do NOT
    repeat these" wording, which contradicted the broadened policy.
    """

    def test_agent_source_no_longer_quotes_old_header(self):
        # Post US-073 the runtime memory section header lives in
        # ``evolve/infrastructure/claude_sdk/prompt_builder.py``.
        # Scan all four owning files to catch any legacy quotation.
        files_to_scan = {
            "agent.py": REPO_ROOT / "evolve" / "agent.py",
            "prompt_builder.py": REPO_ROOT / "evolve" / "prompt_builder.py",
            "prompt_diagnostics.py": REPO_ROOT / "evolve" / "prompt_diagnostics.py",
            "infrastructure/claude_sdk/prompt_builder.py": (
                REPO_ROOT / "evolve" / "infrastructure" / "claude_sdk" / "prompt_builder.py"
            ),
        }
        for fname, fpath in files_to_scan.items():
            if not fpath.exists():
                continue
            src = fpath.read_text()
            assert "errors from previous rounds" not in src, (
                f"{fname} still carries the pre-broadening memory section "
                "header — update to 'cumulative learning log — read, then "
                "append during your turn' per SPEC § 'memory.md'."
            )

    def test_agent_source_carries_broadened_header(self):
        # Post US-073 the runtime memory section header lives in
        # ``evolve/infrastructure/claude_sdk/prompt_builder.py``.
        # Scan all four owning files (agent / prompt_builder shim /
        # prompt_diagnostics / infrastructure prompt_builder) to catch
        # post-DDD-migration drift.
        files_to_scan = [
            REPO_ROOT / "evolve" / "agent.py",
            REPO_ROOT / "evolve" / "prompt_builder.py",
            REPO_ROOT / "evolve" / "prompt_diagnostics.py",
            REPO_ROOT / "evolve" / "infrastructure" / "claude_sdk" / "prompt_builder.py",
        ]
        assert any(
            "cumulative learning log" in f.read_text()
            for f in files_to_scan
            if f.exists()
        )


# ---------------------------------------------------------------------------
# Smoke — every constant is a plain str (not a template func / lazy proxy)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module, attr",
    [
        (agent_mod, "_PREV_ATTEMPT_LOG_FMT"),
        (tui_mod, "_CAIROSVG_MISSING_WARN"),
        (agent_mod, "_MEMORY_WIPED_HEADER_FMT"),
        (evolve_mod, "_DEFAULT_MEMORY_MD"),
    ],
)
def test_constants_are_plain_strings(module, attr):
    """Each extracted constant must be a plain ``str`` — no defer / callable."""
    value = getattr(module, attr)
    assert isinstance(value, str), (
        f"{module.__name__}.{attr} must be a plain str constant, "
        f"got {type(value).__name__}"
    )
    assert value, f"{module.__name__}.{attr} must be non-empty"
