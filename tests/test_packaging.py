"""Tests for pyproject.toml packaging metadata.

Verifies that:
- The `evolve` console_scripts entry point resolves correctly
- Optional dependency groups (rich, dev) are properly declared
- Package metadata matches README documentation
"""

import importlib
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore[no-redef]


PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _load_pyproject():
    with open(PYPROJECT, "rb") as f:
        return tomllib.load(f)


class TestPyprojectMetadata:
    """Verify [project] section metadata."""

    def test_project_name(self):
        data = _load_pyproject()
        assert data["project"]["name"] == "evolve"

    def test_requires_python(self):
        data = _load_pyproject()
        assert data["project"]["requires-python"] == ">=3.10"

    def test_has_description(self):
        data = _load_pyproject()
        assert "description" in data["project"]
        assert len(data["project"]["description"]) > 0

    def test_has_version(self):
        data = _load_pyproject()
        assert "version" in data["project"]


class TestEntryPoint:
    """Verify the console_scripts entry point resolves."""

    def test_console_scripts_declared(self):
        data = _load_pyproject()
        scripts = data["project"]["scripts"]
        assert "evolve" in scripts

    def test_entry_point_format(self):
        """Entry point should be 'module:function' format."""
        data = _load_pyproject()
        ep = data["project"]["scripts"]["evolve"]
        assert ":" in ep, f"Entry point {ep!r} should be 'module:callable'"
        module, func = ep.split(":", 1)
        assert module == "evolve"
        assert func == "main"

    def test_entry_point_module_importable(self):
        """The module referenced by the entry point must be importable."""
        data = _load_pyproject()
        module_name = data["project"]["scripts"]["evolve"].split(":")[0]
        mod = importlib.import_module(module_name)
        assert mod is not None

    def test_entry_point_callable_exists(self):
        """The callable referenced by the entry point must exist and be callable."""
        data = _load_pyproject()
        ep = data["project"]["scripts"]["evolve"]
        module_name, func_name = ep.split(":", 1)
        mod = importlib.import_module(module_name)
        func = getattr(mod, func_name, None)
        assert func is not None, f"{func_name!r} not found in {module_name}"
        assert callable(func), f"{module_name}:{func_name} is not callable"


class TestOptionalDependencies:
    """Verify optional dependency groups match README."""

    def test_rich_group_exists(self):
        data = _load_pyproject()
        opt = data["project"]["optional-dependencies"]
        assert "rich" in opt

    def test_rich_group_contains_rich(self):
        data = _load_pyproject()
        opt = data["project"]["optional-dependencies"]
        assert "rich" in opt["rich"]

    def test_dev_group_exists(self):
        data = _load_pyproject()
        opt = data["project"]["optional-dependencies"]
        assert "dev" in opt

    def test_dev_group_contains_pytest(self):
        data = _load_pyproject()
        deps = data["project"]["optional-dependencies"]["dev"]
        assert any("pytest" in d for d in deps)

    def test_dev_group_contains_rich(self):
        """Dev group should include rich for full-featured development."""
        data = _load_pyproject()
        deps = data["project"]["optional-dependencies"]["dev"]
        assert any("rich" in d for d in deps)


class TestBuildSystem:
    """Verify build-system configuration."""

    def test_build_backend(self):
        data = _load_pyproject()
        assert data["build-system"]["build-backend"] == "setuptools.build_meta"

    def test_build_requires(self):
        data = _load_pyproject()
        requires = data["build-system"]["requires"]
        assert any("setuptools" in r for r in requires)

    def test_py_modules_declared(self):
        """All four modules from README Architecture should be declared."""
        data = _load_pyproject()
        modules = data["tool"]["setuptools"]["py-modules"]
        for expected in ["evolve", "loop", "agent", "tui"]:
            assert expected in modules, f"{expected!r} missing from py-modules"

    def test_core_dependency(self):
        """claude-agent-sdk must be a core dependency."""
        data = _load_pyproject()
        deps = data["project"]["dependencies"]
        assert any("claude-agent-sdk" in d for d in deps)
