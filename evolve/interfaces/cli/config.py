"""evolve CLI config-resolution helpers.

Migrated from ``evolve/cli_config.py`` to
``evolve/interfaces/cli/config.py`` (US-082) as part of the DDD
migration program.

Public symbols (``EFFORT_LEVELS``, ``_validate_effort``,
``_load_config``, ``_resolve_config``) are re-exported through the
shim chain: ``evolve.cli`` → ``evolve.cli_config`` → this module.

Leaf module: imports only stdlib at module top — never ``evolve.agent``
/ ``evolve.orchestrator`` / ``evolve.cli`` — so it can be imported by
``evolve.cli`` without cycles.  ``tomllib`` (Python 3.11+) and ``tomli``
(fallback) are imported lazily inside ``_load_config`` to keep the
module-top imports minimal and to honor the project's
"degrade-gracefully without TOML" contract.
"""

import argparse
import os
import sys
import warnings
from pathlib import Path


#: Accepted values for the ``--effort`` flag / ``effort`` config key /
#: ``EVOLVE_EFFORT`` env var — SPEC.md § "The --effort flag".
EFFORT_LEVELS = ("low", "medium", "high", "max")


def _validate_effort(value: str) -> str:
    """Argparse ``type=`` validator for the ``--effort`` flag.

    Accepts only the four documented literals: ``low``, ``medium``,
    ``high``, ``max``.  Raises :class:`argparse.ArgumentTypeError` on any
    other value so the CLI exits with a clear message rather than
    failing opaquely inside ``ClaudeAgentOptions``.
    """
    if value not in EFFORT_LEVELS:
        raise argparse.ArgumentTypeError(
            f"invalid effort level: {value!r} "
            f"(must be one of: {', '.join(EFFORT_LEVELS)})"
        )
    return value


def _load_config(project_dir: Path) -> dict:
    """Load configuration from evolve.toml or pyproject.toml [tool.evolve].

    Resolution order (handled by caller — this returns file-level config):
    1. CLI flags (caller handles)
    2. Environment variables (caller handles)
    3. evolve.toml in project root
    4. pyproject.toml [tool.evolve] section
    5. Built-in defaults (caller handles)

    Args:
        project_dir: Root directory of the project to load config from.

    Returns:
        A dict of configuration values, or empty dict if no config found.
    """
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:
            return {}

    # Try evolve.toml first
    evolve_toml = project_dir / "evolve.toml"
    if evolve_toml.is_file():
        try:
            with open(evolve_toml, "rb") as f:
                return tomllib.load(f)
        except Exception:
            return {}

    # Fall back to pyproject.toml [tool.evolve]
    pyproject_toml = project_dir / "pyproject.toml"
    if pyproject_toml.is_file():
        try:
            with open(pyproject_toml, "rb") as f:
                data = tomllib.load(f)
            return data.get("tool", {}).get("evolve", {})
        except Exception:
            return {}

    return {}


def _resolve_config(args, project_dir: Path) -> argparse.Namespace:
    """Merge CLI args with config file settings.

    Resolution order (first wins):
    1. CLI flags
    2. Environment variables
    3. evolve.toml / pyproject.toml [tool.evolve]
    4. Built-in defaults

    Settings are resolved via a data-driven loop over field definitions,
    eliminating per-field duplication.

    Args:
        args: Parsed argparse Namespace from the CLI.
        project_dir: Root directory of the project.

    Returns:
        The mutated args Namespace with resolved values.
    """
    file_config = _load_config(project_dir)

    # Field definitions: (name, env_var, default, type[, config_key])
    # type is "str", "int", "bool", or "float" — controls parsing of env/file
    # values and CLI-set detection logic.  Optional 5th element overrides the
    # config-file key (defaults to name).
    fields = [
        ("check", "EVOLVE_CHECK", None, "str"),
        ("rounds", "EVOLVE_ROUNDS", 10, "int"),
        ("timeout", "EVOLVE_TIMEOUT", 20, "int"),
        ("model", "EVOLVE_MODEL", "claude-opus-4-6", "str"),
        ("allow_installs", "EVOLVE_ALLOW_INSTALLS", False, "bool"),
        ("spec", "EVOLVE_SPEC", None, "str"),
        ("capture_frames", "EVOLVE_CAPTURE_FRAMES", False, "bool"),
        ("effort", "EVOLVE_EFFORT", "medium", "str"),
        ("max_cost", "EVOLVE_MAX_COST", None, "float", "max_cost_usd"),
    ]

    # Deprecated fallback: check old yolo config/env if new name not found
    # (handled after the main loop)

    for field in fields:
        name, env_var, default, ftype = field[:4]
        config_key = field[4] if len(field) > 4 else name
        current = getattr(args, name, None)

        # Step 1: Check if CLI flag was explicitly set
        if ftype == "bool":
            cli_set = bool(current)
        elif ftype in ("int", "float"):
            cli_flag = f"--{name.replace('_', '-')}"
            cli_set = any(
                a == cli_flag or a.startswith(f"{cli_flag}=") for a in sys.argv
            )
        else:  # str
            cli_set = current is not None

        if cli_set:
            continue  # CLI wins

        # Step 2: Check environment variable
        env_val = os.environ.get(env_var, "")
        if env_val:
            if ftype == "int":
                try:
                    setattr(args, name, int(env_val))
                except ValueError:
                    pass  # invalid int — leave as-is
                continue  # env was present; skip file/default regardless
            elif ftype == "float":
                try:
                    setattr(args, name, float(env_val))
                    continue  # valid float from env — skip file/default
                except ValueError:
                    pass  # invalid float — fall through to file/default
            elif ftype == "bool":
                if env_val.lower() in ("1", "true", "yes"):
                    setattr(args, name, True)
                    continue
            else:  # str
                setattr(args, name, env_val)
                continue

        # Step 3: Check config file
        if config_key in file_config:
            file_val = file_config[config_key]
            if ftype == "int":
                setattr(args, name, int(file_val))
            elif ftype == "float":
                setattr(args, name, float(file_val))
            elif ftype == "bool":
                setattr(args, name, bool(file_val))
            elif file_val:  # str — only set if truthy (non-empty)
                setattr(args, name, file_val)
                continue
            if ftype not in ("str",):
                continue

        # Step 4: Apply default (only if not already set)
        if getattr(args, name, None) is None:
            setattr(args, name, default)

    # Deprecated fallback: if allow_installs is still False, check old
    # yolo config/env names and emit DeprecationWarning if found.
    if not getattr(args, "allow_installs", False):
        _yolo_env = os.environ.get("EVOLVE_YOLO", "")
        if _yolo_env.lower() in ("1", "true", "yes"):
            warnings.warn(
                "EVOLVE_YOLO is deprecated, use EVOLVE_ALLOW_INSTALLS instead",
                DeprecationWarning,
                stacklevel=2,
            )
            args.allow_installs = True
        elif "yolo" in file_config and file_config["yolo"]:
            warnings.warn(
                "'yolo' config key is deprecated, use 'allow_installs' instead",
                DeprecationWarning,
                stacklevel=2,
            )
            args.allow_installs = True
        elif "allow_installs" in file_config and file_config["allow_installs"]:
            args.allow_installs = True

    return args
