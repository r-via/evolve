"""Tests for Mechanism A — Pre-convergence README audit.

Covers the helpers added to loop.py:

- ``_extract_spec_claims`` — claim extraction (flags, subcommands, env vars,
  requirements, shell examples)
- ``_suggest_readme_section`` — README section hinting
- ``_audit_readme_sync`` — the full audit (idempotency, no-op paths, and
  improvements.md append behavior)

See SPEC.md § "README sync discipline" § Mechanism A for the contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from loop import (
    _audit_readme_sync,
    _enforce_readme_sync_gate,
    _extract_spec_claims,
    _has_unresolved_readme_sync_items,
    _suggest_readme_section,
)


# ---------------------------------------------------------------------------
# _extract_spec_claims
# ---------------------------------------------------------------------------


class TestExtractSpecClaims:
    """Verify the grep-level claim extractor covers the five claim families."""

    def test_empty_spec_returns_empty_list(self) -> None:
        assert _extract_spec_claims("") == []

    def test_cli_flag_headers_detected(self) -> None:
        spec = (
            "## CLI flags\n"
            "\n"
            "### The --check flag\n"
            "Describes the check command.\n"
            "\n"
            "### The --spec flag\n"
            "Alternate spec.\n"
        )
        claims = _extract_spec_claims(spec)
        flags = [c for c in claims if c[1] == "flag"]
        assert ("--check", "flag", "The --check flag") in flags
        assert ("--spec", "flag", "The --spec flag") in flags

    def test_env_vars_detected(self) -> None:
        spec = "Set `EVOLVE_MODEL` or `EVOLVE_SPEC=...` to override defaults.\n"
        claims = _extract_spec_claims(spec)
        env_claims = {c[0] for c in claims if c[1] == "env_var"}
        assert "EVOLVE_MODEL" in env_claims
        assert "EVOLVE_SPEC" in env_claims

    def test_subcommand_detected_and_prose_filtered(self) -> None:
        spec = (
            "Run `evolve start` to begin; `evolve init` creates config.\n"
            "Meanwhile evolve automatically picks up changes and evolve works.\n"
        )
        claims = _extract_spec_claims(spec)
        subs = {c[0] for c in claims if c[1] == "subcommand"}
        assert "evolve start" in subs
        assert "evolve init" in subs
        # Prose filler words should be blocked:
        assert "evolve automatically" not in subs
        assert "evolve works" not in subs

    def test_requirements_bullets_detected(self) -> None:
        spec = (
            "## Requirements\n"
            "\n"
            "- Python 3.10+\n"
            "- `claude-agent-sdk`: pip install claude-agent-sdk\n"
            "- `rich` (optional): pip install rich\n"
            "\n"
            "## Other\n"
            "- not a requirement\n"
        )
        claims = _extract_spec_claims(spec)
        reqs = {c[0] for c in claims if c[1] == "requirement"}
        assert "Python 3.10+" in reqs
        assert "claude-agent-sdk" in reqs
        # Bullet outside Requirements section must not appear
        assert "not a requirement" not in reqs

    def test_shell_examples_in_bash_fence(self) -> None:
        spec = (
            "Example:\n"
            "\n"
            "```bash\n"
            "$ evolve start . --check \"pytest\"\n"
            "evolve start --forever\n"
            "```\n"
        )
        claims = _extract_spec_claims(spec)
        shell = {c[0] for c in claims if c[1] == "shell_example"}
        assert any("evolve start . --check" in s for s in shell)
        assert any("evolve start --forever" in s for s in shell)

    def test_non_bash_fence_not_treated_as_shell(self) -> None:
        spec = (
            "```python\n"
            "evolve_start()\n"
            "```\n"
            "\n"
            "### The --foo flag\n"
            "Description.\n"
        )
        claims = _extract_spec_claims(spec)
        shell = [c for c in claims if c[1] == "shell_example"]
        flags = [c for c in claims if c[1] == "flag"]
        assert shell == []
        # The ``` closing the python fence must not be mistaken for an
        # opening bash fence — the flag header after it must still be found.
        assert ("--foo", "flag", "The --foo flag") in flags

    def test_deduplication(self) -> None:
        spec = (
            "### The --check flag\n"
            "### The --check flag\n"
            "`EVOLVE_MODEL` and again EVOLVE_MODEL\n"
        )
        claims = _extract_spec_claims(spec)
        flag_count = sum(1 for c in claims if c == ("--check", "flag", "The --check flag"))
        env_count = sum(1 for c in claims if c[:2] == ("EVOLVE_MODEL", "env_var"))
        assert flag_count == 1
        assert env_count == 1

    def test_multiple_claim_types_together(self) -> None:
        spec = (
            "# Spec\n"
            "\n"
            "## Requirements\n"
            "- Python 3.10+\n"
            "\n"
            "## CLI flags\n"
            "### The --validate flag\n"
            "Set `EVOLVE_SPEC` to override.\n"
            "\n"
            "```bash\n"
            "$ evolve start . --validate\n"
            "```\n"
        )
        claims = _extract_spec_claims(spec)
        types = {c[1] for c in claims}
        assert {"flag", "env_var", "requirement", "shell_example"}.issubset(types)


# ---------------------------------------------------------------------------
# _suggest_readme_section
# ---------------------------------------------------------------------------


class TestSuggestReadmeSection:
    def test_known_types(self) -> None:
        assert _suggest_readme_section("flag") == "Usage"
        assert _suggest_readme_section("subcommand") == "Usage"
        assert _suggest_readme_section("env_var") == "Configuration"
        assert _suggest_readme_section("requirement") == "Requirements"
        assert _suggest_readme_section("shell_example") == "Examples"

    def test_unknown_type_fallback(self) -> None:
        # Unknown types fall back to a generic "README" label so the audit
        # doesn't crash on future claim kinds.
        assert _suggest_readme_section("something-new") == "README"


# ---------------------------------------------------------------------------
# _audit_readme_sync
# ---------------------------------------------------------------------------


class TestAuditReadmeSync:
    """End-to-end audit tests.

    These verify the four no-op paths documented in the helper's docstring
    plus the happy-path append behavior and idempotency.
    """

    def _write(self, root: Path, spec: str, readme: str) -> Path:
        (root / "SPEC.md").write_text(spec)
        (root / "README.md").write_text(readme)
        imp = root / "improvements.md"
        imp.write_text("# Improvements\n")
        return imp

    def test_noop_when_spec_is_readme(self, tmp_path: Path) -> None:
        imp = tmp_path / "improvements.md"
        imp.write_text("# Improvements\n")
        (tmp_path / "README.md").write_text("### The --foo flag\n")
        assert _audit_readme_sync(tmp_path, imp, spec="README.md") == 0
        assert _audit_readme_sync(tmp_path, imp, spec=None) == 0

    def test_noop_when_spec_missing(self, tmp_path: Path) -> None:
        imp = tmp_path / "improvements.md"
        imp.write_text("# Improvements\n")
        (tmp_path / "README.md").write_text("# readme\n")
        assert _audit_readme_sync(tmp_path, imp, spec="MISSING.md") == 0

    def test_noop_when_readme_missing(self, tmp_path: Path) -> None:
        imp = tmp_path / "improvements.md"
        imp.write_text("# Improvements\n")
        (tmp_path / "SPEC.md").write_text("### The --foo flag\n")
        assert _audit_readme_sync(tmp_path, imp, spec="SPEC.md") == 0

    def test_noop_when_no_gaps(self, tmp_path: Path) -> None:
        imp = self._write(
            tmp_path,
            spec="### The --foo flag\nDescription.\n",
            readme="README mentions --foo here.\n",
        )
        assert _audit_readme_sync(tmp_path, imp, spec="SPEC.md") == 0

    def test_appends_items_for_gaps(self, tmp_path: Path) -> None:
        imp = self._write(
            tmp_path,
            spec=(
                "### The --brand-new flag\n"
                "Does something.\n"
                "\n"
                "Set `EVOLVE_BRAND_NEW` to override.\n"
            ),
            readme="# Project\n\nNo mention of the new feature here.\n",
        )
        count = _audit_readme_sync(tmp_path, imp, spec="SPEC.md")
        assert count == 2
        text = imp.read_text()
        assert "- [ ] [functional] README sync: mention `--brand-new`" in text
        assert "in Usage" in text
        assert "documented in SPEC.md § The --brand-new flag" in text
        assert "`EVOLVE_BRAND_NEW`" in text
        assert "in Configuration" in text

    def test_idempotent_on_second_run(self, tmp_path: Path) -> None:
        imp = self._write(
            tmp_path,
            spec="### The --brand-new flag\nDoes something.\n",
            readme="# Project\n",
        )
        first = _audit_readme_sync(tmp_path, imp, spec="SPEC.md")
        second = _audit_readme_sync(tmp_path, imp, spec="SPEC.md")
        assert first == 1
        assert second == 0
        # The item must appear exactly once. The marker we count is the
        # backtick-wrapped claim `\`--brand-new\``, which only appears in
        # the "mention `<claim>`" position (not in the "§ section" position).
        assert imp.read_text().count("`--brand-new`") == 1

    def test_case_insensitive_grep(self, tmp_path: Path) -> None:
        """Claim is considered mentioned even if README uses different case."""
        imp = self._write(
            tmp_path,
            spec="### The --foo flag\nDescription.\n",
            readme="Run with --FOO to enable.\n",
        )
        assert _audit_readme_sync(tmp_path, imp, spec="SPEC.md") == 0

    def test_item_format_exact(self, tmp_path: Path) -> None:
        """The improvement line must match the format prescribed in SPEC."""
        imp = self._write(
            tmp_path,
            spec="### The --xyz flag\n",
            readme="# empty\n",
        )
        _audit_readme_sync(tmp_path, imp, spec="SPEC.md")
        text = imp.read_text()
        # Must be a proper unchecked functional checkbox, not [performance]
        # or [needs-package], so the next-round agent treats it normally.
        assert "- [ ] [functional] README sync:" in text
        assert "[needs-package]" not in text
        assert "[performance]" not in text

    def test_does_not_block_convergence(self, tmp_path: Path) -> None:
        """Audit is advisory — it never raises and always returns a count."""
        # Corrupted-but-existing improvements.md should still work
        (tmp_path / "SPEC.md").write_text("### The --q flag\n")
        (tmp_path / "README.md").write_text("# readme\n")
        imp = tmp_path / "improvements.md"
        imp.write_text("")  # intentionally empty / no trailing newline
        count = _audit_readme_sync(tmp_path, imp, spec="SPEC.md")
        assert count >= 1

    def test_custom_spec_path_in_subdir(self, tmp_path: Path) -> None:
        """--spec docs/spec.md must be resolved relative to project_dir."""
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "spec.md").write_text("### The --deep flag\n")
        (tmp_path / "README.md").write_text("# readme\n")
        imp = tmp_path / "improvements.md"
        imp.write_text("# Improvements\n")
        count = _audit_readme_sync(tmp_path, imp, spec="docs/spec.md")
        assert count == 1
        assert "docs/spec.md" in imp.read_text()


# ---------------------------------------------------------------------------
# Sanity check against the project's own spec/readme
# ---------------------------------------------------------------------------


class TestAuditAgainstRealProject:
    """Smoke test: run the audit against this repo's real SPEC.md/README.md."""

    def test_runs_without_error(self, tmp_path: Path) -> None:
        project_root = Path(__file__).resolve().parent.parent
        spec_src = project_root / "SPEC.md"
        readme_src = project_root / "README.md"
        if not (spec_src.is_file() and readme_src.is_file()):
            pytest.skip("Real SPEC.md / README.md not available in this checkout")

        (tmp_path / "SPEC.md").write_text(spec_src.read_text())
        (tmp_path / "README.md").write_text(readme_src.read_text())
        imp = tmp_path / "improvements.md"
        imp.write_text("# Improvements\n")

        # Must not raise even on a real, long spec.
        count = _audit_readme_sync(tmp_path, imp, spec="SPEC.md")
        assert count >= 0
        if count > 0:
            assert "README sync" in imp.read_text()


# ---------------------------------------------------------------------------
# Idempotency & wontfix-sync — SPEC.md § "Mechanism A" rules (a)-(c)
# ---------------------------------------------------------------------------


class TestAuditIdempotencyBySubstring:
    """Idempotency scans for the ``<claim>`` phrase, not the full item text.

    This is what SPEC § "Idempotency is mandatory" prescribes: a later
    audit must find the same phrase and skip, even if the existing line's
    wording differs slightly from the audit's generated template.
    """

    def _setup(self, tmp_path: Path) -> Path:
        (tmp_path / "SPEC.md").write_text(
            "### The --brand-new flag\nDoes something.\n"
        )
        (tmp_path / "README.md").write_text("# Project\n")
        imp = tmp_path / "improvements.md"
        imp.write_text("# Improvements\n")
        return imp

    def test_skips_when_existing_pending_item_mentions_same_claim(
        self, tmp_path: Path
    ) -> None:
        imp = self._setup(tmp_path)
        # A pre-existing pending item already mentions `--brand-new` with a
        # slightly different wording than the audit would generate.
        imp.write_text(
            "# Improvements\n"
            "- [ ] [functional] README sync: mention `--brand-new` in README "
            "(user asked for this earlier)\n"
        )
        count = _audit_readme_sync(tmp_path, imp, spec="SPEC.md")
        assert count == 0, (
            "audit must skip claims whose phrase is already present in a "
            "pending item, even if the rest of the wording differs"
        )
        # The line count for this claim stays at one.
        assert imp.read_text().count("`--brand-new`") == 1

    def test_skips_when_wontfix_sync_on_pending_line(self, tmp_path: Path) -> None:
        imp = self._setup(tmp_path)
        imp.write_text(
            "# Improvements\n"
            "- [ ] [functional] README sync: mention `--brand-new` "
            "[wontfix-sync: internal flag, not user-visible]\n"
        )
        count = _audit_readme_sync(tmp_path, imp, spec="SPEC.md")
        assert count == 0
        assert imp.read_text().count("`--brand-new`") == 1

    def test_skips_when_wontfix_sync_on_checked_line(self, tmp_path: Path) -> None:
        """SPEC rule (c): the wontfix-sync marker survives across rounds
        even under ``[x]`` items — the future audit must not re-propose it.
        """
        imp = self._setup(tmp_path)
        imp.write_text(
            "# Improvements\n"
            "- [x] [functional] README sync: mention `--brand-new` "
            "[wontfix-sync: implementation detail, not worth documenting]\n"
        )
        count = _audit_readme_sync(tmp_path, imp, spec="SPEC.md")
        assert count == 0, (
            "wontfix-sync marked phrases must never be re-proposed by a "
            "later audit run, even when the item is already checked off"
        )
        # No new line was appended
        assert imp.read_text().count("`--brand-new`") == 1


class TestHasUnresolvedReadmeSyncItems:
    """Convergence-gate helper: which sync items count as unresolved?"""

    def test_empty_file(self, tmp_path: Path) -> None:
        imp = tmp_path / "improvements.md"
        imp.write_text("")
        assert _has_unresolved_readme_sync_items(imp) is False

    def test_missing_file(self, tmp_path: Path) -> None:
        imp = tmp_path / "improvements.md"
        assert _has_unresolved_readme_sync_items(imp) is False

    def test_unchecked_plain_sync_item_is_unresolved(self, tmp_path: Path) -> None:
        imp = tmp_path / "improvements.md"
        imp.write_text(
            "- [ ] [functional] README sync: mention `--foo` in Usage\n"
        )
        assert _has_unresolved_readme_sync_items(imp) is True

    def test_checked_sync_item_is_resolved(self, tmp_path: Path) -> None:
        imp = tmp_path / "improvements.md"
        imp.write_text(
            "- [x] [functional] README sync: mention `--foo` in Usage\n"
        )
        assert _has_unresolved_readme_sync_items(imp) is False

    def test_wontfix_sync_suffix_is_resolved(self, tmp_path: Path) -> None:
        """Even an unchecked item counts as resolved when it has
        ``[wontfix-sync:]`` — SPEC.md § "Escape hatch"."""
        imp = tmp_path / "improvements.md"
        imp.write_text(
            "- [ ] [functional] README sync: mention `--foo` in Usage "
            "[wontfix-sync: internal constant]\n"
        )
        assert _has_unresolved_readme_sync_items(imp) is False

    def test_non_sync_pending_items_ignored(self, tmp_path: Path) -> None:
        """Only README-sync items count toward this gate."""
        imp = tmp_path / "improvements.md"
        imp.write_text(
            "- [ ] [functional] some regular pending improvement\n"
            "- [ ] [performance] another unrelated item\n"
        )
        assert _has_unresolved_readme_sync_items(imp) is False

    def test_mix_of_resolved_and_unresolved(self, tmp_path: Path) -> None:
        imp = tmp_path / "improvements.md"
        imp.write_text(
            "- [x] [functional] README sync: mention `--foo` in Usage\n"
            "- [ ] [functional] README sync: mention `--bar` in Usage\n"
            "- [ ] [functional] README sync: mention `--baz` in Usage "
            "[wontfix-sync: reason]\n"
        )
        # --bar is unchecked without wontfix-sync → unresolved
        assert _has_unresolved_readme_sync_items(imp) is True


class TestEnforceReadmeSyncGate:
    """The convergence gate unlinks CONVERGED when sync items are unresolved."""

    def _setup_converged(self, tmp_path: Path) -> tuple[Path, Path]:
        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)
        converged = run_dir / "CONVERGED"
        converged.write_text("All spec claims verified.")
        imp = tmp_path / "improvements.md"
        imp.write_text("# Improvements\n")
        return converged, imp

    def test_gate_allows_convergence_when_queue_clean(self, tmp_path: Path) -> None:
        converged, imp = self._setup_converged(tmp_path)
        imp.write_text(
            "# Improvements\n"
            "- [x] [functional] README sync: mention `--foo` in Usage\n"
        )
        rejected = _enforce_readme_sync_gate(converged, imp, sync_added=0)
        assert rejected is False
        assert converged.is_file(), "CONVERGED must stand when all sync items resolved"

    def test_gate_rejects_when_audit_just_added_items(self, tmp_path: Path) -> None:
        converged, imp = self._setup_converged(tmp_path)
        rejected = _enforce_readme_sync_gate(converged, imp, sync_added=3)
        assert rejected is True
        assert not converged.is_file(), "CONVERGED marker must be removed"

    def test_gate_rejects_when_prior_sync_items_still_unchecked(
        self, tmp_path: Path
    ) -> None:
        """Even with sync_added=0 (audit idempotent), any pre-existing
        unresolved sync item must block convergence."""
        converged, imp = self._setup_converged(tmp_path)
        imp.write_text(
            "# Improvements\n"
            "- [ ] [functional] README sync: mention `--foo` in Usage\n"
        )
        rejected = _enforce_readme_sync_gate(converged, imp, sync_added=0)
        assert rejected is True
        assert not converged.is_file()

    def test_gate_allows_when_all_sync_items_wontfix_or_checked(
        self, tmp_path: Path
    ) -> None:
        converged, imp = self._setup_converged(tmp_path)
        imp.write_text(
            "# Improvements\n"
            "- [x] [functional] README sync: mention `--foo` in Usage\n"
            "- [ ] [functional] README sync: mention `--bar` in Usage "
            "[wontfix-sync: implementation detail]\n"
        )
        rejected = _enforce_readme_sync_gate(converged, imp, sync_added=0)
        assert rejected is False
        assert converged.is_file()

    def test_gate_is_noop_when_no_converged_marker(self, tmp_path: Path) -> None:
        """If CONVERGED isn't written yet, gate returns False regardless."""
        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)
        converged = run_dir / "CONVERGED"  # intentionally does not exist
        imp = tmp_path / "improvements.md"
        imp.write_text(
            "# Improvements\n"
            "- [ ] [functional] README sync: mention `--foo` in Usage\n"
        )
        rejected = _enforce_readme_sync_gate(converged, imp, sync_added=5)
        assert rejected is False
        assert not converged.exists()


class TestAuditWorkflowEndToEnd:
    """Exercise the full audit → convergence-gate loop.

    Mirrors SPEC.md § "Mechanism A" rule (4): round N queues gaps, round
    N+1 fixes one and re-audits finding N-1 gaps (no duplicates queued),
    …, round N+k finds 0 gaps and convergence proceeds.
    """

    def test_second_audit_adds_no_duplicates(self, tmp_path: Path) -> None:
        (tmp_path / "SPEC.md").write_text(
            "### The --alpha flag\n"
            "### The --beta flag\n"
        )
        (tmp_path / "README.md").write_text("# README\n")
        imp = tmp_path / "improvements.md"
        imp.write_text("# Improvements\n")

        first = _audit_readme_sync(tmp_path, imp, spec="SPEC.md")
        assert first == 2
        second = _audit_readme_sync(tmp_path, imp, spec="SPEC.md")
        assert second == 0, "second invocation must not re-append any item"
        # Each claim appears exactly once in the file
        text = imp.read_text()
        assert text.count("`--alpha`") == 1
        assert text.count("`--beta`") == 1

    def test_convergence_blocked_until_items_checked_or_wontfix(
        self, tmp_path: Path
    ) -> None:
        # Initial state: spec claims with no README mention, CONVERGED written.
        (tmp_path / "SPEC.md").write_text("### The --only flag\n")
        (tmp_path / "README.md").write_text("# README\n")
        imp = tmp_path / "improvements.md"
        imp.write_text("# Improvements\n")
        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)
        converged = run_dir / "CONVERGED"
        converged.write_text("done")

        # Round N — audit queues the gap, gate rejects CONVERGED
        added = _audit_readme_sync(tmp_path, imp, spec="SPEC.md")
        assert added == 1
        assert _enforce_readme_sync_gate(converged, imp, added) is True
        assert not converged.is_file()

        # Round N+1 — agent writes CONVERGED again, audit finds no new
        # gaps (idempotent), but the pre-existing item still blocks.
        converged.write_text("done2")
        added = _audit_readme_sync(tmp_path, imp, spec="SPEC.md")
        assert added == 0
        assert _enforce_readme_sync_gate(converged, imp, added) is True
        assert not converged.is_file()

        # Round N+2 — agent actually updates the README to mention the
        # claim AND checks off the sync item; gate now allows.
        (tmp_path / "README.md").write_text("# README\n\nUse `--only` to ...\n")
        text = imp.read_text().replace(
            "- [ ] [functional] README sync: mention `--only`",
            "- [x] [functional] README sync: mention `--only`",
        )
        imp.write_text(text)
        converged.write_text("done3")
        added = _audit_readme_sync(tmp_path, imp, spec="SPEC.md")
        assert added == 0  # claim now in README → audit finds no gap
        assert _enforce_readme_sync_gate(converged, imp, added) is False
        assert converged.is_file()

    def test_wontfix_phrase_never_re_proposed(self, tmp_path: Path) -> None:
        """Rule (c): once a claim is wontfix-sync'd, future audits skip it
        even after the original item has been checked off and archived."""
        (tmp_path / "SPEC.md").write_text("### The --internal flag\n")
        (tmp_path / "README.md").write_text("# README\n")
        imp = tmp_path / "improvements.md"
        imp.write_text(
            "# Improvements\n"
            "- [x] [functional] README sync: mention `--internal` "
            "[wontfix-sync: internal-only, not user facing]\n"
        )

        for _ in range(5):
            # Every subsequent round — the claim is still a spec-vs-README
            # gap, but the wontfix-sync marker must make the audit skip it.
            added = _audit_readme_sync(tmp_path, imp, spec="SPEC.md")
            assert added == 0, "wontfix-sync must persist across rounds"

        # Body unchanged — no new line appended on any of the 5 runs
        assert imp.read_text().count("`--internal`") == 1
