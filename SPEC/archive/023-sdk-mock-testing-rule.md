# Hard rule: tests MUST NOT call the real Claude SDK

> Archived from SPEC.md § "Development" on 2026-04-28.
> Stub in SPEC.md points here.

---

Every test that exercises code paths touching the Claude Agent SDK —
``analyze_and_fix``, ``run_claude_agent``, ``run_dry_run_agent``,
``run_validate_agent``, ``run_sync_readme_claude_agent``,
``_run_party_agent_async``, or any helper that builds a
``ClaudeAgentOptions`` — MUST mock the SDK before invocation.  A
test that reaches a live SDK call is a **correctness bug**, not a
cost concern: live calls add variable latency (seconds to minutes
per test), blow past the 20-second pytest ceiling
(§ "The --timeout flag"), burn tokens, require an API key in CI,
and make the suite non-deterministic.

**Required mocking patterns** (choose the smallest one that fits):

1. **Mock ``run_claude_agent`` directly** — cheapest.  For tests
   that exercise ``analyze_and_fix``'s retry or log-writing
   behaviour without caring about the SDK's internals:
   ```python
   with patch("evolve.agent.run_claude_agent", new=AsyncMock()):
       analyze_and_fix(...)
   ```
2. **Mock the ``ClaudeSDKClient`` / ``query`` import** — for tests
   that exercise the message-streaming path.  ``conftest.py``
   installs a ``claude_agent_sdk`` stub in ``sys.modules`` when
   the real SDK is not present; tests that need more control can
   ``patch.dict(sys.modules, {"claude_agent_sdk": fake_sdk})``
   with a bespoke fake exposing the exact classes and async
   iterators needed.
3. **Patch ``asyncio.run``** — for tests that care only about
   "was the agent invoked" and want to bypass the entire async
   stack:
   ```python
   with patch("asyncio.run", side_effect=lambda c: c.close()):
       ...
   ```

**Forbidden patterns:**

- ``import claude_agent_sdk; …`` without a ``sys.modules`` stub
  (the conftest stub is a safety net, not a blessing — relying on
  it to reach the real SDK when the stub isn't installed is the
  same bug).
- Tests that use the ``pytest.mark.skip_if_no_sdk`` marker to
  "run against real SDK locally, skip in CI" — a test that passes
  locally and skips in CI provides no guarantee about either.
- Helper fixtures that instantiate ``ClaudeSDKClient`` and call
  ``.query(...)`` in their ``setup`` — even if the test itself
  intends to only mock afterwards, the fixture has already paid
  the cost and broken the ceiling.

**How to spot a leak.**  If a test's runtime exceeds ~500 ms in
``pytest --durations=10``, it's either spawning a real subprocess
(legitimate only for ``test_entry_point_integrity.py`` and similar
deliberately-integration tests) or leaking into the SDK.  Audit
such tests on sight: run them in isolation under
``pytest --no-summary -q --timeout=5`` and if they hang or call
out, they've got a leak that needs mocking.
