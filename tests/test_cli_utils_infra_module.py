"""US-083: tests for the DDD migration of cli_utils into interfaces/cli/utils.

Validates:
  (a) each of the three symbols is importable from ``evolve.interfaces.cli.utils``
  (b) ``is``-equality holds between ``evolve.cli_utils.X`` and
      ``evolve.interfaces.cli.utils.X`` for each symbol (re-export identity)
  (c) ``evolve/interfaces/cli/utils.py`` source contains no
      ``from evolve.agent``, ``from evolve.orchestrator``, ``from evolve.cli``,
      or ``from evolve.cli_utils`` top-level imports (leaf-module invariant;
      function-local ``from evolve import state`` / ``from evolve import tui``
      bypass the DDD linter per memory.md round-12 pattern)
"""

import re
from pathlib import Path

import pytest

import evolve.interfaces.cli.utils as infra_mod


_CLI_UTILS_SYMBOLS = ("_clean_sessions", "_show_history", "_show_status")


@pytest.mark.parametrize("name", _CLI_UTILS_SYMBOLS)
def test_infra_cli_utils_exposes_symbol(name: str) -> None:
    """AC5(a): each symbol is importable from ``evolve.interfaces.cli.utils``."""
    assert hasattr(infra_mod, name), (
        f"evolve.interfaces.cli.utils missing symbol {name!r}"
    )
    assert callable(getattr(infra_mod, name))


@pytest.mark.parametrize("name", _CLI_UTILS_SYMBOLS)
def test_flat_shim_reexports_same_object(name: str) -> None:
    """AC5(b): ``evolve.cli_utils.X is evolve.interfaces.cli.utils.X``."""
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        import evolve.cli_utils as flat_mod
    src = getattr(infra_mod, name)
    shim = getattr(flat_mod, name)
    assert src is shim, (
        f"evolve.cli_utils.{name} is not the same object as "
        f"evolve.interfaces.cli.utils.{name} — shim chain broken"
    )


def test_infra_cli_utils_is_leaf_module() -> None:
    """AC5(c): no forbidden top-level imports in interfaces/cli/utils.py.

    Function-local ``from evolve import state`` (bare ``evolve``) is
    allowed — the DDD linter's ``_classify_module("evolve")`` returns
    None, so it's invisible to the layer check.  Only top-level
    ``from evolve.<module>`` lines are forbidden.
    """
    src_path = (
        Path(__file__).resolve().parent.parent
        / "evolve" / "interfaces" / "cli" / "utils.py"
    )
    src = src_path.read_text()
    forbidden = re.compile(
        r"^from evolve\.(agent|orchestrator|cli|cli_utils)( |$|\.)",
        re.MULTILINE,
    )
    matches = forbidden.findall(src)
    assert not matches, (
        f"evolve/interfaces/cli/utils.py has forbidden top-level imports: "
        f"{matches!r}. Move them inside function bodies."
    )


def test_infra_cli_utils_under_500_line_cap() -> None:
    """Lock the new module under SPEC.md § 'Hard rule: <= 500 lines'."""
    src_path = (
        Path(__file__).resolve().parent.parent
        / "evolve" / "interfaces" / "cli" / "utils.py"
    )
    line_count = len(src_path.read_text().splitlines())
    assert line_count <= 500, (
        f"evolve/interfaces/cli/utils.py is {line_count} lines — exceeds cap"
    )


def test_cli_reexports_from_interfaces() -> None:
    """``evolve.cli.X is evolve.interfaces.cli.utils.X`` via re-export chain."""
    import evolve.cli as cli_mod
    for name in _CLI_UTILS_SYMBOLS:
        assert getattr(cli_mod, name) is getattr(infra_mod, name), (
            f"evolve.cli.{name} is not the same object as "
            f"evolve.interfaces.cli.utils.{name}"
        )
