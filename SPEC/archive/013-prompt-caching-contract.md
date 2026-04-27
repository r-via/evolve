# 013 — Prompt Caching Contract

> Archived from SPEC.md § "Prompt caching" on 2026-04-27. Stable SDK
> integration pattern — designed once around claude-agent-sdk 0.1.50,
> working as specified.

---

## Prompt caching

Every agent round's prompt concatenates the persona system text,
``SPEC.md``, ``README.md``, and project context — tens of
thousands of tokens of static-ish content.  If the underlying
runtime does not cache this stable portion between calls, every
round pays the full input-token cost even though the content
rarely changes.

**SDK contract (claude-agent-sdk 0.1.50).**  The Python SDK's
``ClaudeAgentOptions.system_prompt`` signature is ``str |
SystemPromptPreset | None`` — it does NOT accept the Anthropic
API's ``list[dict]`` shape with explicit ``cache_control``
markers.  Passing a list silently mis-serialises and the API call
arrives with no usable system prompt (symptom: model returns
zero tool calls on well-formed rounds).

**How caching actually happens.**  The underlying Claude Code
CLI that the SDK wraps applies prompt caching natively on stable
system prompts across calls — the caller does NOT need to set
``cache_control`` explicitly.  When the same (or leading-prefix
identical) system prompt is sent within the cache TTL, the CLI
translates it into a ``cache_control`` API call under the hood
and the response's ``ResultMessage.usage`` carries
``cache_read_input_tokens > 0``.

**Caller contract (what evolve code must do).**

- Pass ``system_prompt`` as a **single string** to
  ``ClaudeAgentOptions``.  Never a list-of-dicts.
- Keep the **leading portion** of the prompt stable across
  rounds — put per-round variable content (check results,
  memory, attempt marker, prior audit, crash diagnostics)
  **after** the static content.  The CLI's caching is prefix-
  based: the cached portion is whatever's identical up to the
  first byte that differs.
- Observe cache hits via ``ResultMessage.usage.cache_read_input_tokens``
  and record them in ``usage_round_N.json``.

**Wrong patterns (will silently disable caching):**

- Two-block ``system_prompt=[dict, dict]`` with explicit
  ``cache_control`` (doesn't match the SDK signature — see
  symptom above).
- Per-round content interleaved with static content (breaks the
  leading-prefix hash).
- A timestamp or counter in the first ~200 bytes of the system
  prompt (invalidates the prefix every call).

**Acceptance criteria for verification:**

1. A session-level integration test runs two rounds back-to-back
   with identical inputs and asserts that the second round's
   ``usage_round_2.json`` has ``cache_read_tokens > 0`` —
   evidence the native caching fires.
2. No call site in evolve passes ``system_prompt=[...]`` as a
   list; grep/lint guard in CI.
3. ``build_prompt`` and its siblings place per-round variable
   content **after** the static (system.md + SPEC/README)
   portion.  A unit test asserts ordering on the rendered
   prompt.
