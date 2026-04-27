# 010 — Structural Change Self-Detection Protocol

> Archived from SPEC.md § "Structural change self-detection" on 2026-04-27.
> Stable protocol — self-evolution safety mechanism, fully implemented.

---

### Structural change self-detection

Some improvements rearrange the repository itself — file renames, module
extractions, entry-point changes, package-layout moves. These are **risky
for autonomous evolution**: the pytest suite mocks subprocess invocations
and so passes even when the real subprocess launcher is broken (e.g. the
orchestrator invoking a file that just got moved). A round can land a
"successful" commit that makes the next round fail to start, and the
failure repeats forever because the same broken state is on disk.

To prevent silent self-breakage, the agent detects **structural changes**
before committing and explicitly hands control back to the operator
rather than continuing.

**Scope: self-evolution only.**  ``RESTART_REQUIRED`` is a
*self-evolution* safety protocol.  Its purpose is to protect the
**running orchestrator's Python imports** from going stale after a
rename / `__init__.py` edit / entry-point move.  That is only a
problem when the project being evolved IS evolve's own source tree
— i.e. ``python -m evolve start /path/to/evolve`` where
``/path/to/evolve`` is the same repository that provides the
orchestrator's running code.

When evolve is driving a third-party project (the common case —
``python -m evolve start /path/to/foo``), structural changes in
``foo/`` never touch ``evolve/``'s module layout.  The round
subprocess spawns a fresh Python interpreter per round anyway
(``python -m evolve _round ...``), so target-project renames would
be visible on the next round's first `import` regardless.  The
agent is therefore instructed to **skip the RESTART_REQUIRED
write** when ``{project_dir}`` is not the same repository as
evolve's own source tree.

The orchestrator implements the same check as defense-in-depth
(``_is_self_evolving`` in ``evolve/orchestrator.py``): even if an
agent mistakenly writes ``RESTART_REQUIRED`` on a third-party
project, the orchestrator silently ignores the marker — the file
stays on disk as an audit trail but no exit-3 fires, no operator
is paged.

**What counts as structural.** Any of the following, detected via
`git diff` / `git status` against the pre-round state:

- A file rename (`git diff --diff-filter=R` reports entries)
- A file creation or deletion that is referenced by `import` / `from X` in
  another tracked file (`grep -l` across the project)
- Changes to `pyproject.toml` sections `[project.scripts]`,
  `[tool.setuptools]`, or dependency lists that move an entry point
- Changes to `evolve/__init__.py`, `evolve/__main__.py`, or any `__init__.py`
  that alters module re-exports
- Creation or deletion of `__main__.py` anywhere in the tree
- Changes to `conftest.py` or `tests/conftest.py` that affect test
  collection / import paths

**Agent-side protocol.** When a structural change is detected during
Phase 3, the agent MUST:

1. Complete the code change as planned and verify tests pass
2. Write `COMMIT_MSG` with a mandatory `STRUCTURAL:` prefix on the first
   line, e.g.:
   ```
   STRUCTURAL: feat(git): extract git operations from loop.py into evolve/git.py

   <body>
   ```
3. Write a `RESTART_REQUIRED` marker in the current run directory with:
   ```
   # RESTART_REQUIRED
   reason: <one-line why the process must restart>
   verify: <shell command(s) the operator should run to check the new state>
   resume: <shell command to continue evolution>
   round: <current round number>
   timestamp: <ISO-8601>
   ```
4. **Skip Phase 4 (convergence) for this round** — leave convergence to
   the next run after restart. Do not write `CONVERGED` even if the
   backlog is empty.
5. Return cleanly from the round subprocess so the orchestrator can
   commit the change and honor the marker.

**Orchestrator-side protocol.** After the agent's round subprocess
returns and before starting the next round, the orchestrator:

1. Runs the normal round-end pipeline (commit, push, check, state.json)
2. Checks for `RESTART_REQUIRED` in the run directory
3. If present:
   - Fires a new `on_structural_change` hook with the marker fields as
     env vars
   - Renders a blocking red panel via `ui.structural_change_required(marker)`:
     ```
     +---- Structural Change -- Operator Review Required ----+
     | Round <N> committed a structural change:              |
     |   <commit subject>                                    |
     |                                                       |
     | Reason: <marker.reason>                               |
     |                                                       |
     | Verify before restarting:                             |
     |   $ <marker.verify>                                   |
     |                                                       |
     | When ready to continue:                               |
     |   $ <marker.resume>                                   |
     |                                                       |
     | Or abort and revert:                                  |
     |   $ git reset --hard HEAD~1                           |
     +------------------------------------------------------+
     ```
   - Exits the evolution loop with **exit code 3** (new — "structural
     change, manual restart required")
4. `--forever` mode does **not** bypass this — structural changes are the
   one category of commit that always pauses autonomy. Auto-continuing
   would re-invoke potentially-broken code.

**Detection confidence.** The signals above are heuristic, not perfect.
False negatives are possible (an agent could do something structural the
heuristic misses). The existing entry-point-integrity guards (subprocess
smoke test, pytest-mocked-subprocess regression test) remain in place as
a backup: if self-detection fails but the change breaks the entry point,
the next round's subprocess crash triggers the zero-progress retry and
then the Phase 1 escape hatch. The structural-change protocol is the
preventive layer; the retry/escape guards are the reactive layer.
