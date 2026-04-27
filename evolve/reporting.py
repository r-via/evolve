"""Evolution report generation.

Extracted from diagnostics.py to keep it under the 500-line cap.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from evolve.costs import TokenUsage, estimate_cost
from evolve.state import _count_checked, _count_unchecked, _runs_base


def _generate_evolution_report(
    project_dir: Path,
    run_dir: Path,
    max_rounds: int,
    final_round: int,
    converged: bool,
    capture_frames: bool = False,
) -> None:
    """Generate evolution_report.md summarizing the session.

    Parses conversation logs, commit messages (from git log), and check results
    to produce a timeline table and summary stats.

    Args:
        project_dir: Root directory of the project.
        run_dir: Session directory where the report will be written.
        max_rounds: Maximum rounds configured for the session.
        final_round: Last round that was actually executed.
        converged: Whether the session converged successfully.
    """
    session_name = run_dir.name
    improvements_path = _runs_base(project_dir) / "improvements.md"
    checked = _count_checked(improvements_path)
    unchecked = _count_unchecked(improvements_path)
    status = "CONVERGED" if converged else "MAX_ROUNDS"

    # Build timeline by scanning each round's data
    timeline_rows: list[str] = []
    files_modified: set[str] = set()
    bugs_fixed = 0
    improvements_done = 0
    prev_passed: int | None = None  # track test counts for arrow format

    for r in range(1, final_round + 1):
        # Try to get the commit message for this round from git log
        action = ""
        commit_msg_line = ""
        from_git_log = False
        try:
            git_result = subprocess.run(
                ["git", "log", "--oneline", f"--grep=round {r}", "--grep=evolve", "--all-match", "-1"],
                cwd=str(project_dir), capture_output=True, text=True, timeout=10,
            )
            if git_result.stdout.strip():
                commit_msg_line = git_result.stdout.strip()
                from_git_log = True
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        # Fall back: parse conversation log for COMMIT_MSG content
        if not commit_msg_line:
            convo_path = run_dir / f"conversation_loop_{r}.md"
            if convo_path.is_file():
                convo_text = convo_path.read_text(errors="replace")
                # Look for conventional commit patterns in the conversation
                for line in convo_text.splitlines():
                    m = re.match(r"^(fix|feat|refactor|perf|docs|test|chore)\(.+?\):\s+(.+)", line.strip())
                    if m:
                        commit_msg_line = line.strip()
                        break

        if commit_msg_line:
            # Strip the git hash prefix from 'git log --oneline' output (<hash> <msg>)
            if from_git_log:
                commit_msg_line = commit_msg_line.split(" ", 1)[-1]
            action = commit_msg_line[:70]
        else:
            action = f"round {r}"

        # Count fix vs feat
        if action.startswith("fix"):
            bugs_fixed += 1
        elif action.startswith("feat"):
            improvements_done += 1

        # Parse check results — show arrow format (prev→current) when possible
        tests_info = ""
        check_path = run_dir / f"check_round_{r}.txt"
        cur_passed: int | None = None
        if check_path.is_file():
            check_text = check_path.read_text(errors="replace")
            pass_fail = "PASS" if "PASS" in check_text else "FAIL"
            # Try to extract test counts (pytest format: "N passed")
            m = re.search(r"(\d+)\s+passed", check_text)
            if m:
                cur_passed = int(m.group(1))
                if prev_passed is not None and cur_passed != prev_passed:
                    tests_info = f"{prev_passed}\u2192{cur_passed}"
                else:
                    tests_info = f"{cur_passed} passed"
                m2 = re.search(r"(\d+)\s+failed", check_text)
                if m2:
                    tests_info += f", {m2.group(1)} failed"
            else:
                tests_info = pass_fail
        prev_passed = cur_passed if cur_passed is not None else prev_passed

        # Parse files changed from conversation log (deduplicated)
        round_files: list[str] = []
        seen_files: set[str] = set()
        convo_path = run_dir / f"conversation_loop_{r}.md"
        if convo_path.is_file():
            convo_text = convo_path.read_text(errors="replace")
            # Look for file edit patterns: Edit → filename, Write → filename
            for fm in re.finditer(r"(?:Edit|Write)\s*→?\s*[`]?([^\s`\n]+\.\w+)", convo_text):
                fname = fm.group(1)
                if fname not in seen_files:
                    seen_files.add(fname)
                    round_files.append(fname)
                files_modified.add(fname)

        files_str = ", ".join(round_files[:3]) if round_files else ""
        if len(round_files) > 3:
            files_str += f" (+{len(round_files) - 3})"

        timeline_rows.append(f"| {r} | {action} | {files_str} | {tests_info} |")

    # Build report
    report_lines = [
        "# Evolution Report",
        f"**Project:** {project_dir.name}",
        f"**Session:** {session_name}",
        f"**Rounds:** {final_round}/{max_rounds}",
        f"**Status:** {status}",
        "",
        "## Timeline",
        "| Round | Action | Files Changed | Tests |",
        "|-------|--------|---------------|-------|",
    ]
    report_lines.extend(timeline_rows)
    report_lines.append("")

    # Cost Summary table — per-round token usage from usage_round_N.json
    cost_rows: list[str] = []
    total_usage = TokenUsage()
    report_model: str | None = None
    for r in range(1, final_round + 1):
        usage_path = run_dir / f"usage_round_{r}.json"
        if usage_path.exists():
            try:
                ru = TokenUsage.from_file(usage_path)
                total_usage += ru
                if ru.model:
                    report_model = ru.model
                per_cost = estimate_cost(ru, ru.model or "") if ru.model else None
                cost_str = f"${per_cost:.2f}" if per_cost is not None else "unknown"
                cost_rows.append(
                    f"| {r} | {ru.input_tokens:,} | {ru.output_tokens:,} "
                    f"| {ru.cache_read_tokens:,} | {cost_str} |"
                )
            except (json.JSONDecodeError, KeyError, OSError):
                continue

    if cost_rows:
        total_cost = estimate_cost(total_usage, report_model or "") if report_model else None
        total_cost_str = f"~${total_cost:.2f}" if total_cost is not None else "unknown"
        model_label = f" ({report_model})" if report_model else ""

        report_lines.append("## Cost Summary")
        report_lines.append("| Round | Input Tokens | Output Tokens | Cache Hits | Est. Cost |")
        report_lines.append("|-------|-------------|---------------|------------|-----------|")
        report_lines.extend(cost_rows)
        report_lines.append(f"**Total: {total_cost_str}**{model_label}")
        report_lines.append("")

    report_lines.append("## Summary")
    report_lines.append(f"- {checked} improvements completed")
    report_lines.append(f"- {bugs_fixed} bugs fixed")
    report_lines.append(f"- {len(files_modified)} files modified")
    if unchecked > 0:
        report_lines.append(f"- {unchecked} improvements remaining")
    if cost_rows:
        total_cost_val = estimate_cost(total_usage, report_model or "") if report_model else None
        if total_cost_val is not None:
            report_lines.append(f"- ~${total_cost_val:.2f} estimated API cost")
    report_lines.append("")

    # Add visual timeline section if frame capture is enabled
    if capture_frames:
        frames_dir = run_dir / "frames"
        if frames_dir.is_dir():
            frame_files = sorted(frames_dir.glob("*.png"))
            if frame_files:
                report_lines.append("## Visual timeline")
                report_lines.append("")
                for frame_file in frame_files:
                    # Use relative path from report location into frames/
                    label = frame_file.stem.replace("_", " ").title()
                    report_lines.append(f"### {label}")
                    report_lines.append(f"![{label}](frames/{frame_file.name})")
                    report_lines.append("")

    report_path = run_dir / "evolution_report.md"
    report_path.write_text("\n".join(report_lines))
