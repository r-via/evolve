"""Tests for the RESTART_REQUIRED scoping rule — SPEC § "Structural
change self-detection" → "Scope: self-evolution only".

``RESTART_REQUIRED`` protects the running orchestrator's Python
imports from going stale after a rename / entry-point move.  That
only matters when the project being evolved is evolve's own source
tree.  When evolve is driving a third-party project, the marker
stays on disk as audit trail but the orchestrator ignores it — no
exit 3, no operator pager.
"""

from __future__ import annotations

from pathlib import Path

from evolve.orchestrator import _is_self_evolving


class TestIsSelfEvolving:
    """Pure unit tests for the scope predicate."""

    def test_evolve_own_repo_is_self_evolving(self):
        """Path that contains the running ``evolve/`` package → self."""
        # The evolve package dir is this test's ``evolve.orchestrator``
        # module parent; the project root is its grandparent.
        import evolve.orchestrator as orch
        evolve_pkg = Path(orch.__file__).resolve().parent
        evolve_root = evolve_pkg.parent
        assert _is_self_evolving(evolve_root) is True

    def test_third_party_project_is_not_self_evolving(self, tmp_path: Path):
        """An unrelated tmp dir → not self-evolving."""
        other = tmp_path / "some_other_project"
        other.mkdir()
        assert _is_self_evolving(other) is False

    def test_sibling_of_evolve_root_is_not_self_evolving(self, tmp_path: Path):
        """Sibling directory with similar name is NOT self-evolving —
        comparison is on resolved path equality, not name matching.
        """
        sibling = tmp_path / "evolve"  # Same NAME, different path.
        sibling.mkdir()
        assert _is_self_evolving(sibling) is False

    def test_relative_path_resolves_before_compare(self, tmp_path: Path, monkeypatch):
        """Relative paths are resolved before comparison — a test in
        ``tmp_path`` should not be confused with evolve's real repo
        just because of ``.`` / ``..`` ambiguity.
        """
        monkeypatch.chdir(tmp_path)
        assert _is_self_evolving(Path(".")) is False
        assert _is_self_evolving(Path("./subdir")) is False

    def test_symlink_to_evolve_root_is_self_evolving(self, tmp_path: Path):
        """A symlink pointing at the real evolve root IS self-evolving
        (resolved-path comparison).  Defensive check: path.resolve()
        should normalise both sides before equality.
        """
        import evolve.orchestrator as orch
        evolve_root = Path(orch.__file__).resolve().parent.parent

        link = tmp_path / "evolve_link"
        try:
            link.symlink_to(evolve_root)
        except (OSError, NotImplementedError):
            # Symlinks not supported on this platform — skip rather
            # than fail; the core resolved-path compare is exercised
            # by the other cases.
            import pytest
            pytest.skip("symlinks unavailable on this platform")
        assert _is_self_evolving(link) is True
