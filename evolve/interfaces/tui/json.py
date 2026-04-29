"""Structured JSON output TUI for CI/CD."""

from __future__ import annotations

from pathlib import Path


class JsonTUI:
    """Emit structured JSON events to stdout for CI/CD integration.

    Each call emits a single JSON line with a ``type``, ``timestamp`` (UTC
    ISO-8601), and event-specific fields.  Implements all ``TUIProtocol``
    methods so the orchestrator needs zero changes.  Enabled via ``--json``.
    """

    def __init__(self):
        import json as _json
        from datetime import datetime as _dt, timezone as _tz
        self._json = _json
        self._dt = _dt
        self._tz = _tz

    def _emit(self, event_type: str, **fields) -> None:
        ts = self._dt.now(self._tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        obj = {"type": event_type, "timestamp": ts, **fields}
        print(self._json.dumps(obj), flush=True)

    # -- Protocol methods --------------------------------------------------

    def round_header(self, round_num: int, max_rounds: int,
                     target: str | None = None, checked: int = 0,
                     total: int = 0,
                     estimated_cost_usd: float | None = None) -> None:
        self._emit("round_start", round=round_num, max_rounds=max_rounds,
                    target=target, checked=checked, total=total,
                    estimated_cost_usd=estimated_cost_usd)

    def blocked_message(self, blocked: int) -> None:
        self._emit("blocked", blocked=blocked,
                    message=f"All {blocked} remaining improvement(s) require new packages. "
                            "Re-run with --allow-installs to allow.")

    def check_result(self, label: str, cmd: str, passed: bool | None = None,
                     timeout: bool = False) -> None:
        self._emit("check_result", label=label, cmd=cmd,
                    passed=passed, timeout=timeout)

    def no_check(self) -> None:
        self._emit("no_check")

    def agent_working(self) -> None:
        self._emit("agent_working")

    def agent_tool(self, tool_name: str, tool_input: str) -> None:
        self._emit("agent_tool", tool=tool_name, input=tool_input)

    def agent_done(self, tools_used: int, log_path: str) -> None:
        self._emit("agent_done", tools_used=tools_used, log_path=log_path)

    def agent_text(self, text: str) -> None:
        self._emit("agent_text", text=text)

    def git_status(self, message: str, pushed: bool | None = None,
                   error: str | None = None) -> None:
        self._emit("git_status", message=message, pushed=pushed, error=error)

    def progress_summary(self, checked: int, unchecked: int) -> None:
        self._emit("progress_summary", checked=checked, unchecked=unchecked)

    def converged(self, round_num: int, reason: str) -> None:
        self._emit("converged", round=round_num, reason=reason)

    def max_rounds(self, max_rounds: int, checked: int, unchecked: int) -> None:
        self._emit("max_rounds", max_rounds=max_rounds,
                    checked=checked, unchecked=unchecked)

    def round_failed(self, round_num: int, exit_code: int) -> None:
        self._emit("round_failed", round=round_num, exit_code=exit_code)

    def no_progress(self) -> None:
        self._emit("no_progress")

    def run_dir_info(self, run_dir: str) -> None:
        self._emit("run_dir_info", run_dir=run_dir)

    def party_mode(self) -> None:
        self._emit("party_mode")

    def warn(self, msg: str) -> None:
        self._emit("warn", message=msg)

    def error(self, msg: str) -> None:
        self._emit("error", message=msg)

    def info(self, msg: str) -> None:
        self._emit("info", message=msg)

    def party_results(self, proposal_path: str | None,
                      report_path: str | None) -> None:
        self._emit("party_results", proposal_path=proposal_path,
                    report_path=report_path)

    def uncommitted(self) -> None:
        self._emit("uncommitted")

    def sdk_rate_limited(self, wait: int, attempt: int,
                         max_retries: int) -> None:
        self._emit("sdk_rate_limited", wait=wait, attempt=attempt,
                    max_retries=max_retries)

    def status_header(self, project_dir: str, has_readme: bool) -> None:
        self._emit("status_header", project_dir=project_dir,
                    has_readme=has_readme)

    def status_improvements(self, checked: int, unchecked: int,
                            blocked: int) -> None:
        self._emit("status_improvements", checked=checked,
                    unchecked=unchecked, blocked=blocked)

    def status_no_improvements(self) -> None:
        self._emit("status_no_improvements")

    def status_memory(self, count: int) -> None:
        self._emit("status_memory", count=count)

    def status_session(self, name: str, convos: int, checks: int,
                       converged: bool, reason: str = "") -> None:
        self._emit("status_session", name=name, convos=convos, checks=checks,
                    converged=converged, reason=reason)

    def status_flush(self) -> None:
        self._emit("status_flush")

    def history_empty(self, project_dir: str) -> None:
        self._emit("history_empty", project_dir=project_dir)

    def history_table(self, project_dir: str, rows: list,
                      num_sessions: int, total_rounds: int,
                      total_improvements: int) -> None:
        self._emit("history", project_dir=project_dir, sessions=rows,
                    num_sessions=num_sessions, total_rounds=total_rounds,
                    total_improvements=total_improvements)

    def completion_summary(self, status: str, round_num: int,
                           duration_s: float, improvements: int,
                           bugs_fixed: int, tests_passing: int | None,
                           report_path: str,
                           estimated_cost_usd: float | None = None) -> None:
        self._emit("completion_summary", status=status, round=round_num,
                    duration_s=duration_s, improvements=improvements,
                    bugs_fixed=bugs_fixed, tests_passing=tests_passing,
                    report_path=report_path,
                    estimated_cost_usd=estimated_cost_usd)

    def budget_reached(self, round_num: int, budget_usd: float,
                       spent_usd: float) -> None:
        self._emit("budget_reached", round=round_num,
                    budget_usd=budget_usd, spent_usd=spent_usd)

    def structural_change_required(self, marker: dict) -> None:
        self._emit("structural_change_required",
                    reason=marker.get("reason", ""),
                    verify=marker.get("verify", ""),
                    resume=marker.get("resume", ""),
                    round=marker.get("round", ""),
                    timestamp=marker.get("timestamp", ""))

    def agent_warn(self, message: str) -> None:
        self._emit("agent_warn", message=message)

    def subprocess_output(self, line: str) -> None:
        """Emit a structured JSON event per subprocess output line. The event
        ``type`` is ``subprocess_output`` with the raw line as ``line``
        (ANSI codes preserved — downstream consumers can strip if needed)."""
        self._emit("subprocess_output", line=line.rstrip("\n"))

    def capture_frame(self, label: str) -> Path | None:
        """JSON TUI has no visual to capture — always returns None."""
        return None
