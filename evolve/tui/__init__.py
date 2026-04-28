"""TUI protocol definition and ``get_tui`` factory.

Re-exports ``TUIProtocol``, ``RichTUI``, ``PlainTUI``, ``JsonTUI``,
``get_tui``, ``_has_rich``, ``_use_json``, and ``_CAIROSVG_MISSING_WARN``
so that ``from evolve.tui import …`` works as a drop-in for the old
flat ``tui`` module.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


# Warning emitted when ``capture_frames`` is enabled but the optional
# ``cairosvg`` dependency (from the ``[vision]`` extra) is not installed.
# Kept as a module-level constant so the startup-time availability check in
# :class:`RichTUI.__init__` and the runtime fallback in
# :meth:`RichTUI.capture_frame` emit the same text — see SPEC.md § "Frame
# capture" § Dependencies.  Any future wording change lands in one place.
_CAIROSVG_MISSING_WARN = (
    "capture_frames is enabled but cairosvg is not installed. "
    "Install with: pip install 'evolve[vision]'. "
    "Frame capture will be a no-op until cairosvg is available."
)


@runtime_checkable
class TUIProtocol(Protocol):
    """Protocol enforcing method parity between RichTUI and PlainTUI.

    Both implementations must provide every method listed here.
    Using ``@runtime_checkable`` so ``isinstance()`` checks work at runtime,
    and static type-checkers (mypy / pyright) verify structural conformance.
    """

    def round_header(self, round_num: int, max_rounds: int,
                     target: str | None = ..., checked: int = ...,
                     total: int = ...,
                     estimated_cost_usd: float | None = ...) -> None: ...

    def blocked_message(self, blocked: int) -> None: ...

    def check_result(self, label: str, cmd: str, passed: bool | None = ...,
                     timeout: bool = ...) -> None: ...

    def no_check(self) -> None: ...

    def agent_working(self) -> None: ...

    def agent_tool(self, tool_name: str, tool_input: str) -> None: ...

    def agent_done(self, tools_used: int, log_path: str) -> None: ...

    def agent_text(self, text: str) -> None: ...

    def git_status(self, message: str, pushed: bool | None = ...,
                   error: str | None = ...) -> None: ...

    def progress_summary(self, checked: int, unchecked: int) -> None: ...

    def converged(self, round_num: int, reason: str) -> None: ...

    def max_rounds(self, max_rounds: int, checked: int, unchecked: int) -> None: ...

    def round_failed(self, round_num: int, exit_code: int) -> None: ...

    def no_progress(self) -> None: ...

    def run_dir_info(self, run_dir: str) -> None: ...

    def party_mode(self) -> None: ...

    def warn(self, msg: str) -> None: ...

    def error(self, msg: str) -> None: ...

    def info(self, msg: str) -> None: ...

    def party_results(self, proposal_path: str | None,
                      report_path: str | None) -> None: ...

    def uncommitted(self) -> None: ...

    def sdk_rate_limited(self, wait: int, attempt: int,
                         max_retries: int) -> None: ...

    def status_header(self, project_dir: str, has_readme: bool) -> None: ...

    def status_improvements(self, checked: int, unchecked: int,
                            blocked: int) -> None: ...

    def status_no_improvements(self) -> None: ...

    def status_memory(self, count: int) -> None: ...

    def status_session(self, name: str, convos: int, checks: int,
                       converged: bool, reason: str = ...) -> None: ...

    def status_flush(self) -> None: ...

    def history_empty(self, project_dir: str) -> None: ...

    def history_table(self, project_dir: str, rows: list,
                      num_sessions: int, total_rounds: int,
                      total_improvements: int) -> None: ...

    def completion_summary(self, status: str, round_num: int,
                           duration_s: float, improvements: int,
                           bugs_fixed: int, tests_passing: int | None,
                           report_path: str,
                           estimated_cost_usd: float | None = ...) -> None: ...

    def budget_reached(self, round_num: int, budget_usd: float,
                       spent_usd: float) -> None: ...

    def structural_change_required(self, marker: dict) -> None: ...

    def agent_warn(self, message: str) -> None: ...

    def subprocess_output(self, line: str) -> None: ...

    def capture_frame(self, label: str) -> Path | None: ...


def _has_rich() -> bool:
    """Check if rich is available."""
    try:
        import rich  # noqa: F401
        return True
    except ImportError:
        return False


# Module-level flag set by the orchestrator when --json is passed.
_use_json: bool = False


def get_tui(
    *,
    run_dir: str | Path | None = None,
    capture_frames: bool = False,
) -> TUIProtocol:
    """Return a TUI instance — JsonTUI if --json, RichTUI if rich available, else PlainTUI.

    Args:
        run_dir: Session directory for frame capture output (only used by RichTUI).
        capture_frames: If True and RichTUI is active, enable TUI frame capture.
    """
    if _use_json:
        from evolve.tui.json import JsonTUI
        return JsonTUI()
    if _has_rich():
        from evolve.tui.rich import RichTUI
        return RichTUI(run_dir=run_dir, capture_frames=capture_frames)
    from evolve.tui.plain import PlainTUI
    return PlainTUI()


# Re-export classes so ``from evolve.tui import RichTUI`` works.
from evolve.tui.rich import RichTUI  # noqa: E402, F401
from evolve.tui.plain import PlainTUI  # noqa: E402, F401
from evolve.tui.json import JsonTUI  # noqa: E402, F401

__all__ = [
    "TUIProtocol",
    "RichTUI",
    "PlainTUI",
    "JsonTUI",
    "get_tui",
    "_has_rich",
    "_use_json",
    "_CAIROSVG_MISSING_WARN",
]
