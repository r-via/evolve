"""Terminal UI for evolve — rich panels + progress bars with plain text fallback.

When `rich` is installed, provides colored panels, progress bars, and styled output.
Falls back gracefully to plain text when `rich` is not available.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol, runtime_checkable

_log = logging.getLogger(__name__)


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

    def subprocess_output(self, line: str) -> None: ...

    def capture_frame(self, label: str) -> Path | None: ...


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
    """TUI powered by the ``rich`` library.

    Uses rich panels, progress bars, and styled text for a polished
    terminal experience.  Implements all ``TUIProtocol`` methods.
    Falls back to ``PlainTUI`` when rich is unavailable (see ``get_tui``).
    """

    def __init__(self, *, run_dir: str | Path | None = None,
                 capture_frames: bool = False):
        from rich.console import Console
        self._capture_frames = capture_frames
        self._run_dir = Path(run_dir) if run_dir else None
        # record=True accumulates rendered output in an internal buffer
        # for later export via save_svg() — no extra overhead when unused.
        # force_terminal=True makes Rich emit ANSI codes even when stdout is
        # piped (as it is for evolve's round subprocesses), so styling reaches
        # both the user's terminal (via the orchestrator's stdout pipe) and
        # the record buffer for frame capture.
        self.console = Console(
            record=capture_frames,
            force_terminal=True,
            color_system="truecolor",
        )
        self._status_grid = None
        self._cairosvg_warned = False
        # Startup-time availability check: when capture_frames is enabled but
        # the optional [vision] extra is not installed, log a single warning
        # up front (never blocks the run). SPEC.md § "Frame capture" requires
        # the warning at startup rather than deferred to first capture call.
        if capture_frames:
            try:
                import cairosvg  # type: ignore[import-untyped]  # noqa: F401
            except ImportError:
                _log.warning(_CAIROSVG_MISSING_WARN)
                self._cairosvg_warned = True

    def round_header(self, round_num: int, max_rounds: int,
                     target: str | None = None, checked: int = 0,
                     total: int = 0,
                     estimated_cost_usd: float | None = None) -> None:
        """Display a colored panel header for the current round."""
        from rich.panel import Panel
        from rich.progress_bar import ProgressBar
        from rich.text import Text
        from rich.table import Table

        grid = Table.grid(padding=(0, 1), expand=True)
        grid.add_column(ratio=1)
        grid.add_column(justify="right")

        cost_str = f"~${estimated_cost_usd:.2f}" if estimated_cost_usd is not None else ""
        grid.add_row(
            Text(f"EVOLUTION ROUND {round_num}/{max_rounds}", style="bold white"),
            Text(cost_str, style="dim"),
        )

        if target:
            grid.add_row(Text(f"TARGET: {target}", style="cyan"), Text(""))

        if total > 0:
            bar = ProgressBar(total=total, completed=checked, width=30)
            progress_text = Text(f" {checked}/{total} improvements done", style="green")
            progress_table = Table.grid(padding=(0, 1))
            progress_table.add_column()
            progress_table.add_column()
            progress_table.add_row(bar, progress_text)
            grid.add_row(Text("PROGRESS: ", style="bold"), Text(""))
            grid.add_row(progress_table, Text(""))

        panel = Panel(grid, title="[bold blue]evolve[/bold blue]", border_style="blue",
                      width=min(self.console.width, 60))
        self.console.print()
        self.console.print(panel)

    def blocked_message(self, blocked: int) -> None:
        """Show a message when all remaining improvements are blocked."""
        from rich.panel import Panel
        msg = (f"[yellow]ALL {blocked} remaining improvement(s) require new packages.[/yellow]\n"
               f"Re-run with [bold]--allow-installs[/bold] to allow package installation.")
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
            self.console.print(f"  [yellow]\\[git][/yellow] {message} (push failed: {error or 'unknown'})")

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
        assert self._status_grid is not None, "status_header() must be called before status_improvements()"
        status = f"[green]{checked} done[/green], [yellow]{unchecked} remaining[/yellow]"
        if blocked > 0:
            status += f" ([red]{blocked} blocked (needs-package)[/red])"
        self._status_grid.add_row("Improvements:", status)

    def status_no_improvements(self) -> None:
        assert self._status_grid is not None, "status_header() must be called before status_no_improvements()"
        self._status_grid.add_row("Improvements:", "[dim](none yet)[/dim]")

    def status_memory(self, count: int) -> None:
        assert self._status_grid is not None, "status_header() must be called before status_memory()"
        self._status_grid.add_row("Memory:", f"{count} entries" if count else "[dim](empty)[/dim]")

    def status_session(self, name: str, convos: int, checks: int,
                       converged: bool, reason: str = "") -> None:
        assert self._status_grid is not None, "status_header() must be called before status_session()"
        self._status_grid.add_row("Latest session:", f"{name} ({convos} rounds, {checks} checks)")
        if converged:
            self._status_grid.add_row("Converged:", f"[bold green]YES[/bold green]")
            if reason:
                self._status_grid.add_row("Reason:", reason[:200])
        else:
            self._status_grid.add_row("Converged:", "[yellow]NO[/yellow]")

    def status_flush(self) -> None:
        assert self._status_grid is not None, "status_header() must be called before status_flush()"
        from rich.panel import Panel
        self.console.print()
        self.console.print(Panel(self._status_grid, title="[bold blue]evolve status[/bold blue]",
                                 border_style="blue"))

    def history_empty(self, project_dir: str) -> None:
        self.console.print(f"\n  No evolution history found for {project_dir}")

    def history_table(self, project_dir: str, rows: list,
                      num_sessions: int, total_rounds: int,
                      total_improvements: int) -> None:
        from rich.table import Table
        from rich.panel import Panel

        self.console.print(f"\n  [bold]Evolution History:[/bold] {project_dir}")
        self.console.print(f"  {'─' * 38}\n")

        table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
        table.add_column("Session", style="cyan")
        table.add_column("Rounds", justify="right")
        table.add_column("Status")
        table.add_column("Improvements")

        for row in rows:
            status_style = "green" if row["status"] == "CONVERGED" else "yellow"
            improvements = f"{row['checked']} done, {row['unchecked']} remaining"
            table.add_row(
                row["name"],
                row["rounds"],
                f"[{status_style}]{row['status']}[/{status_style}]",
                improvements,
            )

        self.console.print(table)
        self.console.print(
            f"\n  Total: {num_sessions} sessions, {total_rounds} rounds, "
            f"{total_improvements} improvements"
        )

    def completion_summary(self, status: str, round_num: int,
                           duration_s: float, improvements: int,
                           bugs_fixed: int, tests_passing: int | None,
                           report_path: str,
                           estimated_cost_usd: float | None = None) -> None:
        """Display a rich panel summarising the completed evolution session."""
        from rich.panel import Panel
        from rich.table import Table

        # Format duration as Xm Ys
        mins, secs = divmod(int(duration_s), 60)
        dur = f"{mins}m {secs:02d}s" if mins else f"{secs}s"

        icon = "\u2705" if status == "CONVERGED" else "\u26a0\ufe0f"
        grid = Table.grid(padding=(0, 1))
        grid.add_column()
        grid.add_row(f"{icon} {status} in {round_num} rounds ({dur})")
        grid.add_row("")
        grid.add_row(f"{improvements} improvements completed")
        grid.add_row(f"{bugs_fixed} bugs fixed")
        if tests_passing is not None:
            grid.add_row(f"{tests_passing} tests passing")
        if estimated_cost_usd is not None:
            grid.add_row(f"~${estimated_cost_usd:.2f} estimated cost")
        grid.add_row("")
        grid.add_row(f"Report: {report_path}")

        border = "green" if status == "CONVERGED" else "yellow"
        panel = Panel(grid, title="[bold]Evolution Complete[/bold]", border_style=border,
                      width=min(self.console.width, 50))
        self.console.print()
        self.console.print(panel)

    def budget_reached(self, round_num: int, budget_usd: float,
                       spent_usd: float) -> None:
        """Display a rich panel when the session's budget cap is exceeded."""
        from rich.panel import Panel
        from rich.table import Table

        grid = Table.grid(padding=(0, 1))
        grid.add_column()
        grid.add_row(f"\u26a0\ufe0f  Session paused at round {round_num}")
        grid.add_row(f"Budget: ${budget_usd:.2f} / Used: ${spent_usd:.2f}")
        grid.add_row("Use --resume with a higher --max-cost to continue")

        panel = Panel(grid, title="[bold]Budget Reached[/bold]",
                      border_style="yellow",
                      width=min(self.console.width, 50))
        self.console.print()
        self.console.print(panel)

    def structural_change_required(self, marker: dict) -> None:
        """Display a blocking red panel for structural changes requiring restart."""
        from rich.panel import Panel
        from rich.table import Table

        grid = Table.grid(padding=(0, 1))
        grid.add_column()
        grid.add_row(f"Round {marker.get('round', '?')} committed a structural change:")
        grid.add_row(f"  {marker.get('reason', '(no reason)')}")
        grid.add_row("")
        grid.add_row("Verify before restarting:")
        grid.add_row(f"  $ {marker.get('verify', '(none)')}")
        grid.add_row("")
        grid.add_row("When ready to continue:")
        grid.add_row(f"  $ {marker.get('resume', '(none)')}")
        grid.add_row("")
        grid.add_row("Or abort and revert:")
        grid.add_row("  $ git reset --hard HEAD~1")

        panel = Panel(
            grid,
            title="[bold red]Structural Change \u2014 Operator Review Required[/bold red]",
            border_style="red",
            width=min(self.console.width, 60),
        )
        self.console.print()
        self.console.print(panel)

    def subprocess_output(self, line: str) -> None:
        """Forward a line of subprocess stdout through the Rich console.

        Routing through ``console.out()`` (instead of raw ``sys.stdout``)
        is what makes subprocess output land in the record buffer so
        frame capture includes it. ``highlight=False, markup=False`` keep
        Rich from re-interpreting the subprocess's own styling — ANSI
        codes emitted by the subprocess (when ``force_terminal=True``) are
        passed through to the terminal verbatim.
        """
        self.console.out(line, end="", highlight=False, markup=False)

    def capture_frame(self, label: str) -> Path | None:
        """Snapshot the recorded Rich buffer as a PNG frame.

        Returns the path to the PNG, or ``None`` when capture is disabled,
        no run directory is set, or ``cairosvg`` is not installed.
        """
        if not self._capture_frames or not self._run_dir:
            return None

        frames_dir = self._run_dir / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)

        svg_path = frames_dir / f"{label}.svg"
        png_path = frames_dir / f"{label}.png"

        # Export the Rich buffer to SVG (built-in, no extra dep)
        self.console.save_svg(str(svg_path))

        # Convert SVG → PNG via cairosvg (optional dependency)
        try:
            import cairosvg  # type: ignore[import-untyped]
        except ImportError:
            if not self._cairosvg_warned:
                _log.warning(_CAIROSVG_MISSING_WARN)
                self._cairosvg_warned = True
            # Clean up the SVG since we can't convert it
            svg_path.unlink(missing_ok=True)
            return None

        try:
            cairosvg.svg2png(url=str(svg_path), write_to=str(png_path))
        except Exception as exc:
            _log.warning("Frame capture failed for %s: %s", label, exc)
            svg_path.unlink(missing_ok=True)
            return None

        # Remove intermediate SVG
        svg_path.unlink(missing_ok=True)
        return png_path


# ---------------------------------------------------------------------------
# Plain text fallback
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# JSON TUI — structured JSON events for CI/CD
# ---------------------------------------------------------------------------

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

    def subprocess_output(self, line: str) -> None:
        """Emit a structured JSON event per subprocess output line. The event
        ``type`` is ``subprocess_output`` with the raw line as ``line``
        (ANSI codes preserved — downstream consumers can strip if needed)."""
        self._emit("subprocess_output", line=line.rstrip("\n"))

    def capture_frame(self, label: str) -> Path | None:
        """JSON TUI has no visual to capture — always returns None."""
        return None


# ---------------------------------------------------------------------------
# Factory — pick the right implementation
# ---------------------------------------------------------------------------

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
        return JsonTUI()
    if _has_rich():
        return RichTUI(run_dir=run_dir, capture_frames=capture_frames)
    return PlainTUI()
