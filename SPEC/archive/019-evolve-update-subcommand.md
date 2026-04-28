# `evolve update` Subcommand (archived from SPEC.md)

*Archived: 2026-04-28 — complete subcommand specification, fully designed*

---

### `evolve update`

One-shot subcommand that pulls the latest commit of evolve itself
from the upstream git repository, so an operator running a
long-lived ``--forever`` session (or just keeping their install
current) doesn't have to drop into the source tree, ``git pull``,
and re-install manually.

```bash
# Pull latest main from upstream
evolve update

# Dry-run: show what would change without applying
evolve update --dry-run

# Pull a specific ref instead of the default branch
evolve update --ref some-branch
evolve update --ref v1.2.3
```

**How it works.**

1. Resolves the install location via ``pip show evolve`` and
   inspects whether the install is **editable** (``pip install -e
   .``) or **non-editable** (snapshot copy under
   ``site-packages/``).
2. Editable install — the install location IS the git working
   tree.  ``evolve update``:
   - Refuses to proceed if the working tree is dirty (uncommitted
     changes, untracked tracked-paths) — the operator must
     explicitly stash, commit, or discard before updating.
     Exception: if the only dirty path is ``.evolve/`` (run
     artifacts), the dirty check ignores it.
   - Runs ``git fetch origin`` followed by ``git merge --ff-only
     <ref>``.  Refuses non-fast-forward (the operator must rebase
     or reset manually — ``evolve update`` is for staying in sync,
     not for rewriting history).
   - Editable installs reflect file changes immediately in the
     next ``python -m evolve`` invocation, so no reinstall step is
     needed.
3. Non-editable install — the install location is a snapshot under
   ``site-packages/`` and ``git`` cannot operate on it.
   ``evolve update``:
   - Resolves the upstream repo URL from package metadata
     (``Project-URL: source = https://github.com/...``).
   - Clones (or fetches into) ``~/.cache/evolve/upstream/`` —
     a per-user cache so repeated updates re-use the clone.
   - Checks out the requested ref.
   - Runs ``pip install --upgrade <cache-path>`` so the
     ``site-packages`` snapshot is replaced with the new version.
4. Either path emits the resulting commit SHA + branch + commit
   subject on stdout, plus a one-line summary of what changed
   (``N files updated, M insertions, K deletions``).

**Safety rails.**

- ``evolve update`` MUST NOT run while another ``evolve start``
  session is active in the same project tree.  The orchestrator
  may be importing modules, mid-round; pulling new files under it
  is the same hazard as a structural commit (SS "Structural change
  self-detection").  The detection is best-effort: a ``.evolve/
  state.json`` whose ``status`` is not in {``CONVERGED``,
  ``ERROR``, ``ABORTED``} blocks the update with a clear error
  pointing at the active session.  ``--force`` bypasses (operator
  takes responsibility).
- ``evolve update`` MUST NOT touch the operator's project tree —
  it only updates evolve's own source / install.  The current
  working directory's ``.evolve/runs/``, ``improvements.md``,
  ``memory.md``, ``SPEC.md`` are never modified.
- The check is **opt-in** — there is no automatic "pull on every
  start" path.  Self-updating during a round would invalidate the
  in-flight orchestrator's imports without the structural-restart
  protocol; the operator runs ``evolve update`` between sessions,
  not within them.

**Exit codes:**

| Exit Code | Meaning |
|-----------|---------|
| 0 | Updated successfully (or already up-to-date — no fetch needed) |
| 1 | Update needed but blocked (dirty tree, non-fast-forward, active session) — operator action required |
| 2 | Error — could not detect install mode, no upstream URL in metadata, network failure, etc. |

**Use cases.**

- **Long unattended runs.**  Operator launches
  ``evolve-watch start . --check pytest --forever`` for a
  weekend, returns Monday, runs ``evolve update`` to pick up
  upstream improvements before re-launching the watcher.
- **Self-evolution + upstream merge.**  Evolve has been
  self-evolving on a fork; the operator periodically runs
  ``evolve update --ref upstream/main`` to merge mainstream
  improvements into the fork between rounds.
- **CI cron job.**  Nightly job runs ``evolve update --dry-run``
  and emails the operator if a new commit landed upstream — they
  decide when to apply.

**Implementation notes.**

- Lives in ``evolve/cli.py`` as a sibling subcommand to
  ``start`` / ``status`` / ``history`` / ``clean``.  Helper
  logic that wraps ``git`` / ``pip`` calls goes into
  ``evolve/infrastructure/git/`` (after the DDD migration) or
  ``evolve/git.py`` (today).
- Tests MUST cover: editable detection, dirty-tree refusal,
  non-fast-forward refusal, active-session detection, upstream
  URL resolution from metadata, and ``--dry-run`` mode (the
  latter outputs the planned ``git`` / ``pip`` invocations
  without executing them).
