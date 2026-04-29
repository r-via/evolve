# --json flag — event schema and examples

> Archived from SPEC.md § "The --json flag" on 2026-04-29.
> Stub in SPEC.md preserves the normative summary.

---

```bash
evolve start ~/projects/my-tool --check "pytest" --json
```

Each line is a JSON object with a `type`, `timestamp`, and event-specific fields:

```json
{"type": "round_start", "timestamp": "2026-03-24T16:00:00Z", "round": 1, "max_rounds": 10}
{"type": "check_result", "timestamp": "2026-03-24T16:00:05Z", "label": "check", "cmd": "pytest", "passed": true}
{"type": "agent_tool", "timestamp": "2026-03-24T16:01:00Z", "tool": "Edit", "input": "src/parser.py"}
{"type": "improvement_completed", "timestamp": "2026-03-24T16:02:00Z", "description": "Add input validation"}
{"type": "converged", "timestamp": "2026-03-24T16:05:00Z", "round": 3, "reason": "All spec claims verified"}
{"type": "hook_fired", "timestamp": "2026-03-24T16:05:01Z", "event": "on_converged", "success": true}
{"type": "usage", "timestamp": "2026-03-24T16:02:01Z", "round": 1, "input_tokens": 45230, "output_tokens": 12400, "estimated_cost_usd": 1.24}
{"type": "budget_reached", "timestamp": "2026-03-24T16:10:00Z", "budget_usd": 10.0, "spent_usd": 10.24}
```

The `JsonTUI` class implements the same `TUIProtocol` as `RichTUI` and
`PlainTUI`, ensuring all output methods are available in JSON mode with zero
changes to business logic.
