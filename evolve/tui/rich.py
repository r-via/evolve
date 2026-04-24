"""Rich-based TUI implementation with frame capture."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from evolve.tui import _CAIROSVG_MISSING_WARN


# Control characters invalid in XML 1.0 except ``\x1B`` (ANSI escape —
# consumed by ``Text.from_ansi`` in ``subprocess_output``) and
# ``\t \n \r`` (always valid).  Subprocess output (``pytest`` in
# particular) can occasionally emit stray ``\x00`` / ``\x07`` / ``\x0B``
# that would otherwise survive into the recorded Rich buffer and break
# ``cairosvg`` when it parses the SVG with "not well-formed (invalid
# token)".  Strip them on the way in.
_XML_INVALID_CTRL = re.compile(r"[\x00-\x08\x0B-\x0C\x0E-\x1A\x1C-\x1F]")

_log = logging.getLogger(__name__)


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
        from rich.panel import Panel  # noqa: F401
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
        from rich.panel import Panel  # noqa: F401

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

        Routing through the Rich console (instead of raw ``sys.stdout``)
        is what makes subprocess output land in the record buffer so
        frame capture can include it.  Two sanitisation steps:

        1. Strip XML-invalid control chars (``\\x00``-``\\x08``,
           ``\\x0B``-``\\x0C``, ``\\x0E``-``\\x1A``, ``\\x1C``-``\\x1F``).
           ANSI escape (``\\x1B``) is preserved for step 2.
        2. Parse remaining ANSI escape sequences with
           ``Text.from_ansi`` so styled output survives in both the
           terminal (Rich re-emits matching ANSI) AND the SVG frame
           capture (Rich serialises the styles into ``<tspan>``
           elements with ``fill``/``font`` attributes).

        Without these steps, raw control chars and literal ANSI escapes
        land in the Rich record buffer, ``save_svg`` renders them as
        literal XML text, and ``cairosvg`` rejects the SVG with
        "not well-formed (invalid token)".
        """
        from rich.text import Text

        clean = _XML_INVALID_CTRL.sub("", line)
        self.console.print(Text.from_ansi(clean), end="")

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
