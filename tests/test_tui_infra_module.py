"""Tests for US-084: evolve/tui/ → evolve/interfaces/tui/ migration.

Verifies:
  (a) All symbols importable from evolve.interfaces.tui
  (b) is-equality between evolve.tui.X and evolve.interfaces.tui.X
  (c) No forbidden top-level imports in interfaces/tui/ source files
  (d) All files under 500 lines
"""

import warnings
from pathlib import Path


def _read_source(name: str) -> str:
    """Read a source file from evolve/interfaces/tui/."""
    p = Path(__file__).resolve().parent.parent / "evolve" / "interfaces" / "tui" / name
    return p.read_text()


class TestInterfacesTuiImports:
    """All canonical symbols importable from evolve.interfaces.tui."""

    def test_tui_protocol_importable(self):
        from evolve.interfaces.tui import TUIProtocol
        assert TUIProtocol is not None

    def test_get_tui_importable(self):
        from evolve.interfaces.tui import get_tui
        assert callable(get_tui)

    def test_has_rich_importable(self):
        from evolve.interfaces.tui import _has_rich
        assert callable(_has_rich)

    def test_use_json_importable(self):
        from evolve.interfaces.tui import _use_json
        assert isinstance(_use_json, bool)

    def test_cairosvg_missing_warn_importable(self):
        from evolve.interfaces.tui import _CAIROSVG_MISSING_WARN
        assert isinstance(_CAIROSVG_MISSING_WARN, str)

    def test_rich_tui_importable(self):
        from evolve.interfaces.tui import RichTUI
        assert RichTUI is not None

    def test_plain_tui_importable(self):
        from evolve.interfaces.tui import PlainTUI
        assert PlainTUI is not None

    def test_json_tui_importable(self):
        from evolve.interfaces.tui import JsonTUI
        assert JsonTUI is not None

    def test_rich_tui_from_submodule(self):
        from evolve.interfaces.tui.rich import RichTUI
        assert RichTUI is not None

    def test_plain_tui_from_submodule(self):
        from evolve.interfaces.tui.plain import PlainTUI
        assert PlainTUI is not None

    def test_json_tui_from_submodule(self):
        from evolve.interfaces.tui.json import JsonTUI
        assert JsonTUI is not None


class TestReExportIdentity:
    """evolve.tui.X is evolve.interfaces.tui.X for every re-exported symbol."""

    def test_tui_protocol_identity(self):
        import evolve.interfaces.tui as infra
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            import evolve.tui as flat
        assert flat.TUIProtocol is infra.TUIProtocol

    def test_get_tui_identity(self):
        import evolve.interfaces.tui as infra
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            import evolve.tui as flat
        assert flat.get_tui is infra.get_tui

    def test_has_rich_identity(self):
        import evolve.interfaces.tui as infra
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            import evolve.tui as flat
        assert flat._has_rich is infra._has_rich

    def test_cairosvg_warn_identity(self):
        import evolve.interfaces.tui as infra
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            import evolve.tui as flat
        assert flat._CAIROSVG_MISSING_WARN is infra._CAIROSVG_MISSING_WARN

    def test_rich_tui_identity(self):
        import evolve.interfaces.tui as infra
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            import evolve.tui as flat
        assert flat.RichTUI is infra.RichTUI

    def test_plain_tui_identity(self):
        import evolve.interfaces.tui as infra
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            import evolve.tui as flat
        assert flat.PlainTUI is infra.PlainTUI

    def test_json_tui_identity(self):
        import evolve.interfaces.tui as infra
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            import evolve.tui as flat
        assert flat.JsonTUI is infra.JsonTUI


class TestLayeringInvariant:
    """No forbidden top-level imports in interfaces/tui/ source files."""

    def test_init_no_forbidden_imports(self):
        """__init__.py has no from evolve.agent/orchestrator/cli imports."""
        src = _read_source("__init__.py")
        for line in src.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("from evolve.") and not stripped.startswith("#"):
                # Only evolve.interfaces.tui.* imports allowed
                assert "evolve.interfaces.tui" in stripped, (
                    f"Forbidden top-level import: {stripped}"
                )

    def test_rich_no_forbidden_imports(self):
        """rich.py imports only from evolve.interfaces.tui (same layer)."""
        src = _read_source("rich.py")
        for line in src.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("from evolve.") and not stripped.startswith("#"):
                assert "evolve.interfaces.tui" in stripped, (
                    f"Forbidden import in rich.py: {stripped}"
                )

    def test_plain_no_evolve_imports(self):
        """plain.py has no evolve imports at all (pure stdlib)."""
        src = _read_source("plain.py")
        for line in src.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("from evolve") or stripped.startswith("import evolve"):
                assert False, f"Unexpected evolve import in plain.py: {stripped}"

    def test_json_no_evolve_imports(self):
        """json.py has no evolve imports at all (pure stdlib)."""
        src = _read_source("json.py")
        for line in src.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("from evolve") or stripped.startswith("import evolve"):
                assert False, f"Unexpected evolve import in json.py: {stripped}"


class TestFileSizeCap:
    """All interfaces/tui/ files under 500 lines."""

    def test_init_under_500(self):
        src = _read_source("__init__.py")
        assert len(src.splitlines()) <= 500

    def test_rich_under_500(self):
        src = _read_source("rich.py")
        assert len(src.splitlines()) <= 500

    def test_plain_under_500(self):
        src = _read_source("plain.py")
        assert len(src.splitlines()) <= 500

    def test_json_under_500(self):
        src = _read_source("json.py")
        assert len(src.splitlines()) <= 500
