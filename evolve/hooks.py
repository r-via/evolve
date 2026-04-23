"""Event hooks — loading config, matching events, fire-and-forget execution.

Evolve fires lifecycle events that can trigger external commands. Hooks are
configured in ``evolve.toml`` or ``pyproject.toml [tool.evolve.hooks]``.

Supported events:
  - on_round_start: A new round begins
  - on_round_end: A round completes successfully
  - on_converged: The project reaches convergence
  - on_error: A round fails (crash, stall, or check failure)
  - on_structural_change: A round committed a structural change requiring restart

Hook execution model:
  - Hooks run as fire-and-forget subprocesses with a 30-second timeout
  - A failing hook never blocks the evolution loop — failures are logged
  - Hook commands receive event context via environment variables
    (EVOLVE_SESSION, EVOLVE_ROUND, EVOLVE_STATUS)
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# All recognised lifecycle events.
SUPPORTED_EVENTS = frozenset({
    "on_round_start",
    "on_round_end",
    "on_converged",
    "on_error",
    "on_structural_change",
})

# Maximum time (seconds) a hook subprocess is allowed to run.
HOOK_TIMEOUT = 30


def load_hooks(project_dir: Path) -> dict[str, str]:
    """Load hook configuration from evolve.toml or pyproject.toml.

    Looks for a ``[hooks]`` section in ``evolve.toml`` first, then
    ``[tool.evolve.hooks]`` in ``pyproject.toml``.  Only keys matching
    ``SUPPORTED_EVENTS`` are returned.

    Args:
        project_dir: Root directory of the project.

    Returns:
        A dict mapping event name to shell command string.
        Empty dict if no hooks are configured or config files are missing.
    """
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:
            return {}

    hooks_section: dict = {}

    # Try evolve.toml first
    evolve_toml = project_dir / "evolve.toml"
    if evolve_toml.is_file():
        try:
            with open(evolve_toml, "rb") as f:
                data = tomllib.load(f)
            hooks_section = data.get("hooks", {})
        except Exception:
            pass

    # Fall back to pyproject.toml [tool.evolve.hooks]
    if not hooks_section:
        pyproject_toml = project_dir / "pyproject.toml"
        if pyproject_toml.is_file():
            try:
                with open(pyproject_toml, "rb") as f:
                    data = tomllib.load(f)
                hooks_section = (
                    data.get("tool", {}).get("evolve", {}).get("hooks", {})
                )
            except Exception:
                pass

    # Filter to supported events only
    return {
        k: str(v) for k, v in hooks_section.items() if k in SUPPORTED_EVENTS
    }


def fire_hook(
    hooks: dict[str, str],
    event: str,
    *,
    session: str = "",
    round_num: int = 0,
    status: str = "",
    extra_env: dict[str, str] | None = None,
) -> bool:
    """Fire a lifecycle event hook if one is configured.

    Executes the hook command as a fire-and-forget subprocess with a
    30-second timeout. Sets environment variables for context:
      - EVOLVE_SESSION: session directory name
      - EVOLVE_ROUND: current round number
      - EVOLVE_STATUS: event-specific status string

    Additional env vars can be passed via *extra_env* (e.g. structural
    change marker fields for ``on_structural_change``).

    Args:
        hooks: Hook configuration dict (from ``load_hooks``).
        event: The event name to fire (e.g. ``"on_round_end"``).
        session: Session directory name for EVOLVE_SESSION env var.
        round_num: Current round number for EVOLVE_ROUND env var.
        status: Status string for EVOLVE_STATUS env var.
        extra_env: Additional environment variables to pass to the hook.

    Returns:
        True if the hook ran successfully, False if it failed or timed out.
        Also returns True if no hook is configured for the event (no-op).
    """
    if event not in SUPPORTED_EVENTS:
        logger.warning("Unknown hook event: %s", event)
        return False

    cmd = hooks.get(event)
    if not cmd:
        return True  # no hook configured — success (no-op)

    env = os.environ.copy()
    env["EVOLVE_SESSION"] = str(session)
    env["EVOLVE_ROUND"] = str(round_num)
    env["EVOLVE_STATUS"] = str(status)
    if extra_env:
        env.update(extra_env)

    try:
        result = subprocess.run(
            cmd,
            shell=True,
            timeout=HOOK_TIMEOUT,
            capture_output=True,
            text=True,
            env=env,
        )
        if result.returncode != 0:
            logger.warning(
                "Hook %s exited with code %d: %s",
                event,
                result.returncode,
                result.stderr[:200] if result.stderr else "(no stderr)",
            )
            return False
        return True
    except subprocess.TimeoutExpired:
        logger.warning("Hook %s timed out after %ds", event, HOOK_TIMEOUT)
        return False
    except Exception as exc:
        logger.warning("Hook %s failed: %s", event, exc)
        return False
