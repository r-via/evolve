"""Tests for the memory.md discipline — SPEC.md § "memory.md".

Covers four contract points the target improvement calls out:

1. The system prompt enforces append-only behavior for the first 500 lines
   (i.e. the prompt teaches the agent to append, not rewrite, until the
   500-line compaction threshold is crossed).
2. The 50%-shrink sanity gate triggers a debug retry with the correct
   "MEMORY WIPED" / "silently wiped memory.md" diagnostic header.
3. The archival path moves old entries to `## Archive` instead of deleting
   them (the prompt teaches archival, not deletion).
4. The new initial template is written when `runs/memory.md` does not yet
   exist (_init_config cold-start scaffold).

Points 2 and 4 have some coverage elsewhere (tests/test_loop_coverage.py,
tests/test_evolve.py); this file pins the contract down in one place and
adds the missing coverage for points 1 and 3.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from evolve.orchestrator import (
    _MEMORY_COMPACTION_MARKER,
    _MEMORY_WIPE_THRESHOLD,
    _run_rounds,
)


# ---------------------------------------------------------------------------
# (Point 1) System prompt — append-only language for the first 500 lines
# ---------------------------------------------------------------------------

class TestSystemPromptAppendOnlyDiscipline:
    """The system prompt must instruct the agent to append (never rewrite)
    memory.md until it crosses ~500 lines.  The language lives in
    prompts/system.md and is rendered by agent.build_prompt.
    """

    def _system_prompt_text(self) -> str:
        path = Path(__file__).resolve().parent.parent / "prompts" / "system.md"
        return path.read_text()

    def test_append_only_default_language_present(self):
        """The default compaction stance is 'append-only'.  The exact
        wording matters — the SPEC quotes this phrase verbatim, so any
        rewrite to something weaker ("prefer appending", "try to append")
        would quietly erode the contract.
        """
        text = self._system_prompt_text()
        # SPEC.md § "Compaction — append-only by default"
        assert "Append-only by default" in text
        assert "append entries during your" in text or "append entries" in text

    def test_five_hundred_line_threshold_documented(self):
        """The 500-line compaction threshold is the *only* trigger for
        touching prior entries.  Below it, memory.md is immutable.  The
        system prompt must make that boundary explicit.
        """
        text = self._system_prompt_text()
        # The exact threshold number is what the orchestrator also uses
        # to decide when compaction is a legitimate operation.
        assert "500" in text
        # Explicit below-threshold immutability clause.
        assert (
            "do not touch prior entries" in text
            or "does not delete existing" in text
            or "does **not**\n  delete existing" in text
        )

    def test_non_obvious_gate_rule_present(self):
        """The 'non-obvious gate' rule keeps memory.md from becoming a
        code-diary.  Stripping this rule would reintroduce the original
        failure mode of useless low-signal entries.
        """
        text = self._system_prompt_text()
        assert "Non-obvious gate" in text

    def test_telegraphic_style_rule_present(self):
        """Entries must be telegraphic — the length cap + style rule is
        how memory.md stays readable across 100+ rounds.
        """
        text = self._system_prompt_text()
        assert "Telegraphic style" in text
        # Length cap must be explicit (≤ 5 lines OR ≤ 400 chars).
        assert "400" in text
        assert "5 lines" in text

    def test_append_only_rule_appears_inside_first_scan_window(self):
        """The agent reads the system prompt top-to-bottom; the append-only
        rule must appear in a position the agent actually scans — not
        buried past the Watchdog / Commit sections.  Concretely: its line
        position must be before the Watchdog section header.
        """
        text = self._system_prompt_text()
        lines = text.splitlines()

        append_line = next(
            (i for i, ln in enumerate(lines) if "Append-only by default" in ln),
            None,
        )
        watchdog_line = next(
            (i for i, ln in enumerate(lines) if ln.startswith("## Watchdog")),
            None,
        )
        assert append_line is not None, "append-only rule missing"
        assert watchdog_line is not None, "watchdog section missing"
        # Append-only rule must come BEFORE the watchdog section so agents
        # encounter the memory discipline in the memory block, not after
        # orchestration concerns.
        assert append_line < watchdog_line


# ---------------------------------------------------------------------------
# (Point 3) System prompt — archive-not-delete language
# ---------------------------------------------------------------------------

class TestSystemPromptArchiveNotDelete:
    """SPEC.md § "Compaction" says: *merge duplicates and archive (do not
    delete) entries older than 20 rounds into a collapsed ## Archive
    section*.  The system prompt must convey archival, not deletion.
    """

    def _system_prompt_text(self) -> str:
        path = Path(__file__).resolve().parent.parent / "prompts" / "system.md"
        return path.read_text()

    def test_archive_section_name_documented(self):
        """The destination section for archived entries has a fixed name
        (`## Archive`) so agents across rounds land on the same header.
        """
        text = self._system_prompt_text()
        assert "## Archive" in text

    def test_archive_verb_explicitly_contrasted_with_delete(self):
        """The prompt must explicitly say 'archive' and 'do not delete' in
        proximity, not just mention an archive section.  Without the
        explicit contrast, a compacting agent could still interpret
        'archive' as a euphemism for deletion.
        """
        text = self._system_prompt_text()
        # Both verbs appear, and the "do not delete" disclaimer comes
        # near the "archive" directive.  We match the canonical wording
        # from SPEC.md § "Compaction — append-only by default".
        assert re.search(r"archive.*do not delete|do not delete.*archive", text, re.I | re.S)

    def test_twenty_round_cutoff_documented(self):
        """The 20-round cutoff for archival is the sole criterion for
        moving entries out of the primary read path.  Without this
        number, compacting agents would pick arbitrary boundaries and
        the archive contract would drift.
        """
        text = self._system_prompt_text()
        assert "20 rounds" in text

    def test_never_empty_unread_section_rule_present(self):
        """The guardrail that stops an over-compacting agent: *if you
        can't tell whether an entry is relevant, keep it*.  Matches
        SPEC.md § "Compaction" bullet 4.
        """
        text = self._system_prompt_text()
        assert "Never empty a section" in text


# ---------------------------------------------------------------------------
# (Point 2) Orchestrator-side 50% shrink sanity gate → MEMORY WIPED retry
# ---------------------------------------------------------------------------

def _make_ui():
    """MagicMock UI that accepts any TUIProtocol call — _run_rounds uses a
    moving target API (round_header, progress_summary, warn, etc.) and
    MagicMock is more forgiving than hand-rolled stubs."""
    return MagicMock()


def _setup_project_with_git(tmp_path: Path, commit_msg: str):
    """Create a project dir with a git repo and seed one commit.

    Returns (project_dir, run_dir, improvements_path, memory_path).
    """
    import os
    import subprocess as sp

    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / "README.md").write_text("# Test project\n")
    runs = project_dir / "runs"
    runs.mkdir()
    run_dir = runs / "session"
    run_dir.mkdir()
    imp_path = runs / "improvements.md"
    imp_path.write_text("- [ ] [functional] do something\n")
    memory_path = runs / "memory.md"

    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@test.com",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@test.com",
    }
    sp.run(["git", "init"], cwd=str(project_dir), capture_output=True)
    sp.run(["git", "add", "-A"], cwd=str(project_dir), capture_output=True, env=env)
    sp.run(
        ["git", "commit", "-m", commit_msg, "--allow-empty"],
        cwd=str(project_dir),
        capture_output=True,
        env=env,
    )
    return project_dir, run_dir, imp_path, memory_path


class TestMemoryWipeSanityGateRetry:
    """The orchestrator-side byte-size sanity gate: any >50% shrink of
    memory.md without `memory: compaction` in the commit body must
    trigger a MEMORY WIPED diagnostic that the agent's prompt builder
    then renders as "CRITICAL — Previous round silently wiped memory.md".
    """

    def test_memory_collapse_without_marker_emits_wiped_diagnostic(self, tmp_path: Path):
        """A silent wipe (full memory.md → header-only) without the
        compaction marker in the commit message produces a MEMORY WIPED
        reason string in the orchestrator's saved diagnostic.
        """
        project_dir, run_dir, imp_path, memory_path = _setup_project_with_git(
            tmp_path, "feat: unrelated change\n\nbody only"
        )
        # Seed >500 bytes of memory so a wipe crosses the threshold.
        memory_path.write_text("# Agent Memory\n\n" + ("entry row\n" * 80))
        assert memory_path.stat().st_size > 500

        diagnostics: list[str] = []
        ui = _make_ui()

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            # Create a conversation log so progress-detection doesn't
            # block on log-size changes.
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round")
            # Real progress on improvements so that OTHER zero-progress
            # branches (imp_unchanged, no_commit_msg) don't also fire.
            imp_path.write_text(
                imp_path.read_text() + f"- [ ] [functional] new {round_num}\n"
            )
            # Restore-then-wipe so the orchestrator's pre-subprocess
            # snapshot sees the full size and the post-subprocess size
            # sees the wipe.
            memory_path.write_text("# Agent Memory\n\n" + ("entry row\n" * 80))
            memory_path.write_text("# Agent Memory\n")
            return 0, "ok", False

        def mock_save_diag(run_dir_, round_num_, cmd_, output_, reason, attempt):
            diagnostics.append(reason)

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._save_subprocess_diagnostic", side_effect=mock_save_diag), \
             patch("evolve.orchestrator._generate_evolution_report"), \
             pytest.raises(SystemExit):
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd=None,
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )

        assert any(d.startswith("MEMORY WIPED: ") for d in diagnostics), diagnostics
        # Threshold percentage referenced via the constant so a future
        # threshold change propagates here without test edits.
        threshold_pct = int(_MEMORY_WIPE_THRESHOLD * 100)
        assert any(
            f"memory.md shrunk by >{threshold_pct}%" in d for d in diagnostics
        )
        assert any(_MEMORY_COMPACTION_MARKER in d for d in diagnostics)

    def test_wipe_diagnostic_renders_dedicated_prompt_header(self, tmp_path: Path):
        """When the diagnostic starts with 'MEMORY WIPED', the agent's
        build_prompt must render the dedicated 'silently wiped memory.md'
        header — NOT the generic 'NO PROGRESS' header.  This is how the
        agent knows to treat the retry as a memory-discipline violation
        rather than a generic stuck round.
        """
        from evolve.agent import build_prompt

        project_dir = tmp_path
        (project_dir / "README.md").write_text("# project")
        runs = project_dir / "runs"
        runs.mkdir()
        (runs / "improvements.md").write_text("- [ ] [functional] do x\n")
        (runs / "memory.md").write_text("# memory\n")
        run_dir = runs / "session"
        run_dir.mkdir()

        threshold_pct = int(_MEMORY_WIPE_THRESHOLD * 100)
        (run_dir / "subprocess_error_round_2.txt").write_text(
            f"Round 2 — MEMORY WIPED: memory.md shrunk by >{threshold_pct}% "
            f"(2000\u21925 bytes) without '{_MEMORY_COMPACTION_MARKER}' "
            f"in commit message (attempt 1)\n"
            f"Output (last 3000 chars):\n...last output...\n"
        )

        prompt = build_prompt(
            project_dir=project_dir,
            run_dir=run_dir,
            round_num=2,
            check_cmd=None,
            check_output=None,
            allow_installs=False,
        )
        assert "CRITICAL — Previous round silently wiped memory.md" in prompt
        assert "Previous round made NO PROGRESS" not in prompt
        # The compaction marker (the documented escape hatch) must be
        # surfaced in the prompt so the agent knows how to legitimize
        # a future compaction.
        assert _MEMORY_COMPACTION_MARKER in prompt


# ---------------------------------------------------------------------------
# (Point 4) Initial template scaffold when runs/memory.md does not exist
# ---------------------------------------------------------------------------

class TestInitialMemoryTemplateScaffold:
    """_init_config writes runs/memory.md on cold start with the four typed
    sections (## Errors, ## Decisions, ## Patterns, ## Insights) so new
    projects start with the expected shape.  Existing memory.md files are
    never overwritten.
    """

    def test_cold_start_creates_memory_md_with_four_sections(self, tmp_path: Path):
        from evolve import _init_config

        target = tmp_path / "brand_new_project"
        assert not (target / "runs" / "memory.md").exists()

        _init_config(target)

        memory = target / "runs" / "memory.md"
        assert memory.is_file(), "runs/memory.md must be created on cold start"
        content = memory.read_text()
        assert content.startswith("# Agent Memory\n")
        # All four typed sections, in documented order.
        assert "\n## Errors\n" in content
        assert "\n## Decisions\n" in content
        assert "\n## Patterns\n" in content
        assert "\n## Insights\n" in content
        errors_idx = content.index("## Errors")
        decisions_idx = content.index("## Decisions")
        patterns_idx = content.index("## Patterns")
        insights_idx = content.index("## Insights")
        assert errors_idx < decisions_idx < patterns_idx < insights_idx

    def test_existing_memory_md_is_not_clobbered(self, tmp_path: Path):
        """If runs/memory.md already exists (e.g. re-running _init_config
        on an established project), the template must NOT overwrite it.
        """
        from evolve import _init_config

        target = tmp_path / "existing_project"
        runs = target / "runs"
        runs.mkdir(parents=True)
        existing = runs / "memory.md"
        existing.write_text(
            "# Pre-existing memory\n\n"
            "### Important entry — round 42\n"
            "critical context we cannot lose\n"
        )
        pre_content = existing.read_text()

        _init_config(target)

        assert existing.read_text() == pre_content, (
            "existing memory.md must not be overwritten by the template scaffold"
        )

    def test_default_template_constant_shape(self):
        """_DEFAULT_MEMORY_MD is the single source of truth for the
        scaffold; it must contain all four section headers.
        """
        from evolve import _DEFAULT_MEMORY_MD

        assert _DEFAULT_MEMORY_MD.startswith("# Agent Memory\n")
        for section in ("## Errors", "## Decisions", "## Patterns", "## Insights"):
            assert section in _DEFAULT_MEMORY_MD
