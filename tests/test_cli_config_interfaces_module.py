"""Lock-in tests for the DDD migration of cli_config into
``evolve/interfaces/cli/config.py`` (US-082).

Verifies:
(a) all 4 symbols importable from the infrastructure module,
(b) ``is``-identity with the flat shim and cli.py re-exports,
(c) leaf-module invariant (no forbidden top-level imports),
(d) file under 500-line cap.
"""

from __future__ import annotations

import re
from pathlib import Path


_CANONICAL_NAMES = (
    "EFFORT_LEVELS",
    "_validate_effort",
    "_load_config",
    "_resolve_config",
)


def test_importable_from_interfaces_module():
    """Every config-resolution symbol must be importable from the
    new interfaces path."""
    from evolve.interfaces.cli.config import (  # noqa: F401
        EFFORT_LEVELS,
        _validate_effort,
        _load_config,
        _resolve_config,
    )


def test_is_identity_with_flat_shim():
    """``evolve.cli_config`` shim re-exports must be ``is``-identical
    to the interfaces module objects."""
    import evolve.cli_config as flat_mod
    import evolve.interfaces.cli.config as iface_mod

    for name in _CANONICAL_NAMES:
        assert getattr(flat_mod, name) is getattr(iface_mod, name), (
            f"evolve.cli_config.{name} must be the SAME object as "
            f"evolve.interfaces.cli.config.{name}"
        )


def test_is_identity_with_cli_reexport():
    """``evolve.cli`` re-exports must be ``is``-identical to the
    interfaces module objects."""
    import evolve.cli as cli_mod
    import evolve.interfaces.cli.config as iface_mod

    for name in _CANONICAL_NAMES:
        assert getattr(cli_mod, name) is getattr(iface_mod, name), (
            f"evolve.cli.{name} must be the SAME object as "
            f"evolve.interfaces.cli.config.{name}"
        )


def test_interfaces_module_is_leaf():
    """No top-level imports from evolve.agent, evolve.orchestrator,
    or evolve.cli in the interfaces module."""
    import evolve.interfaces.cli.config as mod

    src = Path(mod.__file__).read_text()
    violations = re.findall(
        r"^from evolve\.(agent|orchestrator|cli)( |$|\.)",
        src,
        re.MULTILINE,
    )
    assert violations == [], (
        "evolve/interfaces/cli/config.py must be a leaf "
        f"— no forbidden top-level imports. Found: {violations}"
    )


def test_interfaces_module_under_500_lines():
    """SPEC § 'Hard rule: source files MUST NOT exceed 500 lines'."""
    import evolve.interfaces.cli.config as mod

    src = Path(mod.__file__).read_text()
    n = src.count("\n") + (0 if src.endswith("\n") else 1)
    assert n <= 500, (
        f"evolve/interfaces/cli/config.py is {n} lines"
    )


def test_interfaces_cli_init_reexports():
    """``evolve.interfaces.cli`` __init__.py re-exports must be
    ``is``-identical to the config module objects."""
    import evolve.interfaces.cli as cli_pkg
    import evolve.interfaces.cli.config as config_mod

    for name in _CANONICAL_NAMES:
        assert getattr(cli_pkg, name) is getattr(config_mod, name), (
            f"evolve.interfaces.cli.{name} must be the SAME object as "
            f"evolve.interfaces.cli.config.{name}"
        )
