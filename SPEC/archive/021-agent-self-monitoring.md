# Agent-side self-monitoring

*Archived from SPEC.md on 2026-04-28. Stable self-healing mechanism
within the subprocess monitoring section.*

---

On top of the orchestrator's zero-progress detection, the agent itself
inspects the last two rounds' conversation logs (`conversation_loop_{N-1}.md`
and `conversation_loop_{N-2}.md`) at the start of every round and refuses to
repeat a stuck pattern. Specifically, before doing any work the agent:

1. Reads the previous two conversation logs from the current run directory
2. Extracts the improvement target each round was attempting
3. Flags a **stuck loop** if the current target matches either of them and the
   prior round(s) contain no `Edit`/`Write` tool calls -- i.e. pure
   reconnaissance followed by a placeholder commit
4. When stuck is detected, the agent does **not** resume the original target.
   Instead, it:
   - Splits the target in `improvements.md` into smaller independent items
     (one per file, per uncovered line range, per behavior), or
   - Marks the target as blocked with `[blocked: target too broad -- split required]`
     and picks a different unchecked item
5. Logs the decision to `memory.md` so future rounds don't re-attempt the same
   broken split

This makes the agent self-healing for the most common failure mode -- getting
lost in a target that's too large -- without operator intervention. The
orchestrator's zero-progress retry remains the safety net; agent-side detection
is the first line of defense and catches the loop one round earlier.
