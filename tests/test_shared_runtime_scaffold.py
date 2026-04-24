"""Tests for ``_scaffold_shared_runtime_files``.

``improvements.md`` and ``memory.md`` are predictable files that
every evolution session needs.  The orchestrator pre-creates them
at ``{runs_base}`` on session startup so the agent doesn't have to
decide *where* to create them — the files already exist at the
canonical path.  Previous behaviour (relying on the agent's prompt
to write them in the right place) occasionally produced
per-session copies under ``{run_dir}`` instead of the shared
``{runs_base}``.
"""

from __future__ import annotations

from pathlib import Path

from evolve.orchestrator import _scaffold_shared_runtime_files
from evolve.state import _runs_base


class TestScaffoldSharedRuntimeFiles:
    """The scaffold creates both files at ``{runs_base}`` when missing
    and is idempotent — existing files are never overwritten.
    """

    def test_cold_start_creates_both_files(self, tmp_path: Path):
        """Neither file exists → both are created at the canonical path."""
        project = tmp_path / "brand_new"
        project.mkdir()
        _scaffold_shared_runtime_files(project, spec=None)

        runs = _runs_base(project)
        assert (runs / "improvements.md").is_file()
        assert (runs / "memory.md").is_file()

    def test_improvements_template_mentions_us_format(self, tmp_path: Path):
        """The scaffolded improvements.md points at the US-format SPEC."""
        project = tmp_path / "new"
        project.mkdir()
        _scaffold_shared_runtime_files(project, spec=None)

        text = (_runs_base(project) / "improvements.md").read_text()
        assert "# Improvements" in text
        assert "user story" in text.lower() or "user-story" in text.lower()

    def test_memory_template_has_four_sections(self, tmp_path: Path):
        """The scaffolded memory.md contains the documented four typed
        sections in canonical order.
        """
        project = tmp_path / "new"
        project.mkdir()
        _scaffold_shared_runtime_files(project, spec=None)

        text = (_runs_base(project) / "memory.md").read_text()
        # Either the CLI-provided template or the inline fallback —
        # both must contain the four sections.
        for section in ("## Errors", "## Decisions", "## Patterns", "## Insights"):
            assert section in text, f"{section!r} missing from scaffolded memory.md"

    def test_existing_improvements_md_not_overwritten(self, tmp_path: Path):
        """A pre-existing improvements.md (e.g. from a resumed session)
        is never overwritten — the scaffold is idempotent.
        """
        project = tmp_path / "resumed"
        project.mkdir()
        runs = _runs_base(project)
        runs.mkdir(parents=True)
        (runs / "improvements.md").write_text("# keep me\n- [x] existing item\n")

        _scaffold_shared_runtime_files(project, spec=None)

        assert (runs / "improvements.md").read_text() == "# keep me\n- [x] existing item\n"

    def test_existing_memory_md_not_overwritten(self, tmp_path: Path):
        """Same idempotency guarantee for memory.md."""
        project = tmp_path / "resumed"
        project.mkdir()
        runs = _runs_base(project)
        runs.mkdir(parents=True)
        (runs / "memory.md").write_text("# Agent Memory — already populated\n\n## Errors\n### round 1 bug — kept\n")

        _scaffold_shared_runtime_files(project, spec=None)

        text = (runs / "memory.md").read_text()
        assert "already populated" in text
        assert "round 1 bug — kept" in text

    def test_scaffold_creates_runs_base_directory_if_missing(self, tmp_path: Path):
        """Running scaffold on a project with no ``.evolve/`` or ``runs/``
        dir creates the canonical ``.evolve/runs/`` along with the files.
        """
        project = tmp_path / "greenfield"
        project.mkdir()
        assert not (project / ".evolve").exists()
        assert not (project / "runs").exists()

        _scaffold_shared_runtime_files(project, spec=None)

        # Canonical path created (fresh project has no legacy ``runs/``).
        canonical = project / ".evolve" / "runs"
        assert canonical.is_dir()
        assert (canonical / "improvements.md").is_file()
        assert (canonical / "memory.md").is_file()

    def test_scaffold_respects_legacy_runs_fallback(self, tmp_path: Path):
        """When a legacy ``runs/`` directory already exists (mid-migration
        project), the scaffold writes there instead of creating a new
        ``.evolve/runs/`` — matches ``_runs_base`` resolution order.
        """
        project = tmp_path / "legacy"
        project.mkdir()
        (project / "runs").mkdir()

        _scaffold_shared_runtime_files(project, spec=None)

        # Files should land in the legacy location.
        assert (project / "runs" / "improvements.md").is_file()
        assert (project / "runs" / "memory.md").is_file()
        # Canonical .evolve/runs/ should NOT have been pre-created.
        assert not (project / ".evolve" / "runs" / "improvements.md").exists()
