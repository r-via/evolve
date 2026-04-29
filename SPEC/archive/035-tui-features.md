# TUI — feature list and completion summary

> Archived from SPEC.md § "TUI" on 2026-04-29.
> Stub in SPEC.md preserves the normative summary.

---

### TUI features

- Colored panels for round headers with progress bars
- Real-time agent activity feed (tools used, files edited)
- Check command results with pass/fail indicators
- Git commit + push status
- Per-round estimated cost display in round headers
- Completion summary panel on exit (including total cost)
- Budget-reached panel when `--max-cost` is exceeded
- Graceful fallback to plain text when `rich` is not installed
- TUI interface enforced via Protocol — `RichTUI`, `PlainTUI`, and `JsonTUI`
  all implement the same `TUIProtocol`, guaranteeing method parity at
  type-check time
- Optional frame capture: snapshot the rendered TUI as PNG at round end /
  convergence / errors, so party-mode agents can reason visually — see
  "Frame capture" below

### Completion summary

When evolution finishes (converged or max rounds), evolve prints a summary
panel to the terminal. The summary is generated from the session's
`evolution_report.md` and displayed through the TUI (Rich panel, plain text,
or JSON event depending on output mode).
