# Authoritative termination signal from the SDK

> Archived from SPEC.md on 2026-04-28 (round 20, Sid).
> Trigger: every 20 rounds; settled ResultMessage inspection contract, fully implemented.

---

Whether a round hit ``max_turns`` is **not** inferred from indirect tells
(missing ``COMMIT_MSG``, ``imp_unchanged``, etc.) — those remain useful
fallbacks but conflate distinct failure modes. The Claude Agent SDK's
``ResultMessage`` is the authoritative source and MUST be inspected:

```python
@dataclass
class ResultMessage:
    subtype: str           # "success" | "error_max_turns" | "error_during_execution"
    is_error: bool
    num_turns: int
    stop_reason: str | None
    duration_ms: int
    ...
```

Every callsite of ``claude_agent_sdk.query`` in ``evolve/agent.py``
(implement, draft, review, memory curator, and any future agents)
captures the **final** ``ResultMessage`` of the stream and:

1. Logs ``subtype`` and ``num_turns`` on a dedicated line in the
   conversation log (e.g. ``Done: 40 messages, 62 tool calls,
   subtype=error_max_turns, num_turns=40``) so post-mortem analysis no
   longer has to guess from tool-call counts.
2. Prints a console-visible warning when ``is_error=True`` —
   ``⚠ Agent stopped: error_max_turns after 40 turns`` — surfaced via
   ``ui.agent_warn`` so the operator sees the signal in the TUI in
   real time, not only in the log file.
3. Returns the ``subtype`` to the orchestrator as part of the agent's
   result tuple, so ``run_single_round`` can branch the retry logic on
   a precise cause:
   - ``error_max_turns`` → retry with a "fix-only, defer investigation"
     prompt header AND record the granularity violation against the
     current target (candidate for split on next rebuild).
   - ``error_during_execution`` → retry with the SDK error surfaced
     verbatim in the diagnostic.
   - ``success`` + ``imp_unchanged`` → genuine "agent decided no work
     needed" path (e.g. backlog drained, see carve-out above) — do
     NOT conflate with a turn-budget exhaustion.

Until this signal is wired through, the orchestrator's zero-progress
heuristic over-fires on ``success`` rounds where the agent legitimately
made no edits, and under-fires when the agent commits a partial fix
just before hitting the cap. Both bugs disappear once ``subtype`` is
the source of truth.
