"""Terminal UI for evolve — rich panels + progress bars with plain text fallback.

When `rich` is installed, provides colored panels, progress bars, and styled output.
Falls back gracefully to plain text when `rich` is not available.
"""

from __future__ import annotations


def _has_rich() -> bool:
    """Check if rich is available."""
    try:
        import rich  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Rich TUI implementation
# ---------------------------------------------------------------------------

class RichTUI:
    """TUI powered by the `rich` library."""

    def __init__(self):
        from rich.console import Console
        self.console = Console()

    def round_header(self, round_num: int, max_rounds: int,
                     target: str | None = None, checked: int = 0,
                     total: int = 0) -> None:
        """Display a colored panel header for the current round."""
        from rich.panel import Panel
        from rich.progress_bar import ProgressBar
        from rich.text import Text
        from rich.table import Table

        grid = Table.grid(padding=(0, 1))
        grid.add_column()

        grid.add_row(Text(f"EVOLUTION ROUND {round_num}/{max_rounds}", style="bold white"))

        if target:
            grid.add_row(Text(f"TARGET: {target}", style="cyan"))

        if total > 0:
            bar = ProgressBar(total=total, completed=checked, width=30)
            progress_text = Text(f" {checked}/{total} improvements done", style="green")
            progress_table = Table.grid(padding=(0, 1))
            progress_table.add_column()
            progress_table.add_column()
            progress_table.add_row(bar, progress_text)
            grid.add_row(Text("PROGRESS: ", style="bold") )
            grid.add_row(progress_table)

        panel = Panel(grid, title="[bold blue]evolve[/bold blue]", border_style="blue",
                      width=min(self.console.width, 60))
        self.console.print()
        self.console.print(panel)

    def blocked_message(self, blocked: int) -> None:
        """Show a message when all remaining improvements are blocked."""
        from rich.panel import Panel
        msg = (f"[yellow]ALL {blocked} remaining improvement(s) require new packages.[/yellow]\n"
               f"Re-run with [bold]--yolo[/bold] to allow package installation.")
        self.console.print(Panel(msg, title="[yellow]Blocked[/yellow]", border_style="yellow"))

    def check_result(self, label: str, cmd: str, passed: bool | None = None,
                     timeout: bool = False) -> None:
        """Display check command results with pass/fail indicators."""
        if timeout:
            self.console.print(f"  [bold red]\\[{label}][/bold red] TIMEOUT — {cmd}")
        elif passed is None:
            self.console.print(f"  [dim]\\[{label}][/dim] Running: {cmd}")
        elif passed:
            self.console.print(f"  [bold green]\\[{label}] PASS[/bold green] — {cmd}")
        else:
            self.console.print(f"  [bold red]\\[{label}] FAIL[/bold red] — {cmd}")

    def no_check(self) -> None:
        self.console.print("  [dim]\\[check] No check command configured[/dim]")

    def agent_working(self) -> None:
        self.console.print("\n  [bold cyan]\\[agent][/bold cyan] Claude opus working...")

    def agent_tool(self, tool_name: str, tool_input: str) -> None:
        self.console.print(f"  [cyan]\\[opus][/cyan] {tool_name} → {tool_input[:80]}")

    def agent_done(self, tools_used: int, log_path: str) -> None:
        self.console.print(f"  [cyan]\\[opus][/cyan] done ({tools_used} tool calls) → {log_path}")

    def agent_text(self, text: str) -> None:
        self.console.print(text)

    def git_status(self, message: str, pushed: bool | None = None,
                   error: str | None = None) -> None:
        if pushed is None:
            self.console.print(f"  [dim]\\[git] no changes[/dim]")
        elif pushed:
            self.console.print(f"  [green]\\[git][/green] {message} → pushed")
        else:
            self.console.print(f"  [yellow]\\[git][/yellow] {message} (push failed: {error})")

    def progress_summary(self, checked: int, unchecked: int) -> None:
        self.console.print(f"\n  Progress: [green]{checked} done[/green], [yellow]{unchecked} remaining[/yellow]")

    def converged(self, round_num: int, reason: str) -> None:
        from rich.panel import Panel
        self.console.print()
        self.console.print(Panel(
            f"[bold green]CONVERGED at round {round_num}[/bold green]\n{reason}",
            border_style="green", title="[green]Convergence[/green]"
        ))

    def max_rounds(self, max_rounds: int, checked: int, unchecked: int) -> None:
        self.console.print(
            f"\n[yellow]*** Max rounds ({max_rounds}) reached — "
            f"{checked} done, {unchecked} remaining ***[/yellow]"
        )

    def round_failed(self, round_num: int, exit_code: int) -> None:
        self.console.print(f"\n  [red]Round {round_num} failed (exit {exit_code})[/red]")

    def no_progress(self) -> None:
        self.console.print("\n  [yellow]Agent made no progress — stopping.[/yellow]")
        self.console.print("  Is claude-agent-sdk installed? Run: evolve.py --help")

    def run_dir_info(self, run_dir: str) -> None:
        self.console.print(f"  Run directory: [bold]{run_dir}[/bold]")

    def party_mode(self) -> None:
        self.console.print("\n  [bold magenta]Launching Party Mode — multi-agent brainstorming...[/bold magenta]")

    def warn(self, msg: str) -> None:
        self.console.print(f"  [yellow]WARN: {msg}[/yellow]")

    def error(self, msg: str) -> None:
        self.console.print(f"  [bold red]{msg}[/bold red]")

    def info(self, msg: str) -> None:
        self.console.print(msg)

    def party_results(self, proposal_path: str | None, report_path: str | None) -> None:
        if proposal_path:
            self.console.print(f"\n  README_proposal.md → {proposal_path}")
        if report_path:
            self.console.print(f"  party_report.md   → {report_path}")
        if proposal_path:
            self.console.print("  Review and accept/reject. If accepted: cp README_proposal.md README.md && evolve start .")

    def uncommitted(self) -> None:
        self.console.print("[yellow]Uncommitted changes — committing snapshot...[/yellow]")

    def sdk_rate_limited(self, wait: int, attempt: int, max_retries: int) -> None:
        self.console.print(f"  [yellow]\\[sdk] rate limited — waiting {wait}s (attempt {attempt}/{max_retries})...[/yellow]")

    # Status display
    def status_header(self, project_dir: str, has_readme: bool) -> None:
        from rich.panel import Panel
        from rich.table import Table

        grid = Table.grid(padding=(0, 1))
        grid.add_column(style="bold", min_width=15)
        grid.add_column()
        grid.add_row("Project:", project_dir)
        grid.add_row("README:", "[green]exists[/green]" if has_readme else "[red]MISSING[/red]")
        self._status_grid = grid

    def status_improvements(self, checked: int, unchecked: int, blocked: int) -> None:
        status = f"[green]{checked} done[/green], [yellow]{unchecked} remaining[/yellow]"
        if blocked > 0:
            status += f" ([red]{blocked} blocked (needs-package)[/red])"
        self._status_grid.add_row("Improvements:", status)

    def status_no_improvements(self) -> None:
        self._status_grid.add_row("Improvements:", "[dim](none yet)[/dim]")

    def status_memory(self, count: int) -> None:
        self._status_grid.add_row("Memory:", f"{count} entries" if count else "[dim](empty)[/dim]")

    def status_session(self, name: str, convos: int, checks: int,
                       converged: bool, reason: str = "") -> None:
        self._status_grid.add_row("Latest session:", f"{name} ({convos} rounds, {checks} checks)")
        if converged:
            self._status_grid.add_row("Converged:", f"[bold green]YES[/bold green]")
            if reason:
                self._status_grid.add_row("Reason:", reason[:200])
        else:
            self._status_grid.add_row("Converged:", "[yellow]NO[/yellow]")

    def status_flush(self) -> None:
        from rich.panel import Panel
        self.console.print()
        self.console.print(Panel(self._status_grid, title="[bold blue]evolve status[/bold blue]",
                                 border_style="blue"))


# ---------------------------------------------------------------------------
# Plain text fallback
# ---------------------------------------------------------------------------

class PlainTUI:
    """Plain text fallback when `rich` is not installed."""

    def round_header(self, round_num: int, max_rounds: int,
                     target: str | None = None, checked: int = 0,
                     total: int = 0) -> None:
        print(f"\n{'#' * 60}")
        print(f"  EVOLUTION ROUND {round_num}/{max_rounds}")
        if target:
            print(f"  TARGET: {target}")
        if total > 0:
            done = int((checked / total) * 10) if total else 0
            bar = '\u2588' * done + '\u2591' * (10 - done)
            print(f"  PROGRESS: {bar} {checked}/{total} improvements done")
        print(f"{'#' * 60}")

    def blocked_message(self, blocked: int) -> None:
        print(f"  ALL {blocked} remaining improvement(s) require new packages.")
        print(f"  Re-run with --yolo to allow package installation, or add new improvements.")

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
            print(f"  [git] {message} (push failed: {error})")

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


# ---------------------------------------------------------------------------
# Factory — pick the right implementation
# ---------------------------------------------------------------------------

def get_tui() -> RichTUI | PlainTUI:
    """Return a TUI instance — RichTUI if `rich` is available, PlainTUI otherwise."""
    if _has_rich():
        return RichTUI()
    return PlainTUI()
