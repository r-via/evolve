"""Plain-text fallback TUI."""

from __future__ import annotations

import sys
from pathlib import Path


class PlainTUI:
    """Plain text fallback when ``rich`` is not installed.

    Outputs all status information via ``print()`` using plain ASCII.
    Implements all ``TUIProtocol`` methods with no external dependencies.
    """

    def round_header(self, round_num: int, max_rounds: int,
                     target: str | None = None, checked: int = 0,
                     total: int = 0,
                     estimated_cost_usd: float | None = None) -> None:
        print(f"\n{'#' * 60}")
        title = f"  EVOLUTION ROUND {round_num}/{max_rounds}"
        if estimated_cost_usd is not None:
            title += f"  ~${estimated_cost_usd:.2f}"
        print(title)
        if target:
            print(f"  TARGET: {target}")
        if total > 0:
            done = int((checked / total) * 10) if total else 0
            bar = '\u2588' * done + '\u2591' * (10 - done)
            print(f"  PROGRESS: {bar} {checked}/{total} improvements done")
        print(f"{'#' * 60}")

    def blocked_message(self, blocked: int) -> None:
        print(f"  ALL {blocked} remaining improvement(s) require new packages.")
        print(f"  Re-run with --allow-installs to allow package installation, or add new improvements.")

    def check_result(self, label: str, cmd: str, passed: bool | None = None,
                     timeout: bool = False) -> None:
        if timeout:
            print(f"  [{label}] TIMEOUT")
        elif passed is None:
            print(f"\n  [{label}] Running: {cmd}")
        elif passed:
            print(f"  [{label}] PASS (exit 0)")
        else:
            print(f"  [{label}] FAIL")

    def no_check(self) -> None:
        print(f"  [check] No check command configured")

    def agent_working(self) -> None:
        print(f"\n  [agent] Claude opus working...")

    def agent_tool(self, tool_name: str, tool_input: str) -> None:
        print(f"  [opus] {tool_name} → {tool_input[:80]}")

    def agent_done(self, tools_used: int, log_path: str) -> None:
        print(f"  [opus] done ({tools_used} tool calls) → {log_path}")

    def agent_text(self, text: str) -> None:
        print(text)

    def git_status(self, message: str, pushed: bool | None = None,
                   error: str | None = None) -> None:
        if pushed is None:
            print(f"  [git] no changes")
        elif pushed:
            print(f"  [git] {message} → pushed")
        else:
            print(f"  [git] {message} (push failed: {error or 'unknown'})")

    def progress_summary(self, checked: int, unchecked: int) -> None:
        print(f"\n  Progress: {checked} done, {unchecked} remaining")

    def converged(self, round_num: int, reason: str) -> None:
        print(f"\n*** CONVERGED at round {round_num} ***")
        print(f"  {reason}")

    def max_rounds(self, max_rounds: int, checked: int, unchecked: int) -> None:
        print(f"\n*** Max rounds ({max_rounds}) reached — {checked} done, {unchecked} remaining ***")

    def round_failed(self, round_num: int, exit_code: int) -> None:
        print(f"\n  Round {round_num} failed (exit {exit_code})")

    def no_progress(self) -> None:
        print(f"\n  Agent made no progress — stopping.")
        print(f"  Is claude-agent-sdk installed? Run: evolve.py --help")

    def run_dir_info(self, run_dir: str) -> None:
        print(f"  Run directory: {run_dir}")

    def party_mode(self) -> None:
        print("\n  Launching Party Mode — multi-agent brainstorming...")

    def warn(self, msg: str) -> None:
        print(f"  WARN: {msg}")

    def error(self, msg: str) -> None:
        print(msg)

    def info(self, msg: str) -> None:
        print(msg)

    def party_results(self, proposal_path: str | None, report_path: str | None) -> None:
        if proposal_path:
            print(f"\n  README_proposal.md → {proposal_path}")
        if report_path:
            print(f"  party_report.md   → {report_path}")
        if proposal_path:
            print("  Review and accept/reject. If accepted: cp README_proposal.md README.md && evolve start .")

    def uncommitted(self) -> None:
        print("Uncommitted changes — committing snapshot...")

    def sdk_rate_limited(self, wait: int, attempt: int, max_retries: int) -> None:
        print(f"  [sdk] rate limited — waiting {wait}s (attempt {attempt}/{max_retries})...")

    # Status display
    def status_header(self, project_dir: str, has_readme: bool) -> None:
        print(f"\n  Project: {project_dir}")
        print(f"  README:  {'exists' if has_readme else 'MISSING'}")

    def status_improvements(self, checked: int, unchecked: int, blocked: int) -> None:
        status_line = f"  Improvements: {checked} done, {unchecked} remaining"
        if blocked > 0:
            status_line += f" ({blocked} blocked (needs-package))"
        print(status_line)

    def status_no_improvements(self) -> None:
        print(f"  Improvements: (none yet)")

    def status_memory(self, count: int) -> None:
        print(f"  Memory: {count} entries" if count else f"  Memory: (empty)")

    def status_session(self, name: str, convos: int, checks: int,
                       converged: bool, reason: str = "") -> None:
        print(f"  Latest session: {name} ({convos} rounds, {checks} checks)")
        print(f"  Converged: {'YES' if converged else 'NO'}")
        if converged and reason:
            print(f"  Reason: {reason[:200]}")

    def status_flush(self) -> None:
        print()

    def history_empty(self, project_dir: str) -> None:
        print(f"\n  No evolution history found for {project_dir}")

    def history_table(self, project_dir: str, rows: list,
                      num_sessions: int, total_rounds: int,
                      total_improvements: int) -> None:
        print(f"\n  Evolution History: {project_dir}")
        print(f"  {'─' * 38}\n")
        print(f"  {'Session':<21}{'Rounds':<9}{'Status':<12}{'Improvements'}")
        for row in rows:
            improvements = f"{row['checked']} done, {row['unchecked']} remaining"
            print(f"  {row['name']:<21}{row['rounds']:<9}{row['status']:<12}{improvements}")
        print(f"\n  Total: {num_sessions} sessions, {total_rounds} rounds, "
              f"{total_improvements} improvements")

    def completion_summary(self, status: str, round_num: int,
                           duration_s: float, improvements: int,
                           bugs_fixed: int, tests_passing: int | None,
                           report_path: str,
                           estimated_cost_usd: float | None = None) -> None:
        """Display a plain text completion summary."""
        mins, secs = divmod(int(duration_s), 60)
        dur = f"{mins}m {secs:02d}s" if mins else f"{secs}s"
        icon = "\u2705" if status == "CONVERGED" else "\u26a0\ufe0f"
        print(f"\n{'─' * 46}")
        print(f"  {icon} {status} in {round_num} rounds ({dur})")
        print()
        print(f"  {improvements} improvements completed")
        print(f"  {bugs_fixed} bugs fixed")
        if tests_passing is not None:
            print(f"  {tests_passing} tests passing")
        if estimated_cost_usd is not None:
            print(f"  ~${estimated_cost_usd:.2f} estimated cost")
        print()
        print(f"  Report: {report_path}")
        print(f"{'─' * 46}")

    def budget_reached(self, round_num: int, budget_usd: float,
                       spent_usd: float) -> None:
        """Display a plain text budget-reached message."""
        print(f"\n{'─' * 46}")
        print(f"  \u26a0\ufe0f  Session paused at round {round_num}")
        print(f"  Budget: ${budget_usd:.2f} / Used: ${spent_usd:.2f}")
        print(f"  Use --resume with a higher --max-cost to continue")
        print(f"{'─' * 46}")

    def structural_change_required(self, marker: dict) -> None:
        """Display a plain text structural change panel."""
        print(f"\n{'─' * 56}")
        print(f"  Structural Change \u2014 Operator Review Required")
        print(f"{'─' * 56}")
        print(f"  Round {marker.get('round', '?')} committed a structural change:")
        print(f"    {marker.get('reason', '(no reason)')}")
        print()
        print(f"  Verify before restarting:")
        print(f"    $ {marker.get('verify', '(none)')}")
        print()
        print(f"  When ready to continue:")
        print(f"    $ {marker.get('resume', '(none)')}")
        print()
        print(f"  Or abort and revert:")
        print(f"    $ git reset --hard HEAD~1")
        print(f"{'─' * 56}")

    def subprocess_output(self, line: str) -> None:
        """Forward raw subprocess stdout. PlainTUI has no record buffer, so
        this is a direct ``sys.stdout`` write with an explicit flush to
        preserve real-time streaming behavior."""
        sys.stdout.write(line)
        sys.stdout.flush()

    def capture_frame(self, label: str) -> Path | None:
        """Plain text TUI has no visual to capture — always returns None."""
        return None
