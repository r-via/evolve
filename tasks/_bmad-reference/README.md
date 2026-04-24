# BMad reference templates (archived)

These files are **unmodified BMad method templates** kept here as
inspiration material for the evolve-native task docs at the
`tasks/` root (`review-adversarial-round.md`, etc.).

**Evolve does NOT consume these files.**  They are not loaded by any
code in `evolve/`, `loop.py`, `agent.py`, or the prompts.  Party
mode reads `agents/` but not `tasks/`; the orchestrator reads
`prompts/system.md` and — via the round-review wiring — the
`tasks/review-adversarial-round.md` protocol doc.

## Contents

- `review-adversarial-general.xml` — BMad generic adversarial review
  workflow.  Basis for the Zara persona (`agents/reviewer.md`) and
  the round-level protocol in `tasks/review-adversarial-round.md`.
- `code-review/` — BMad senior-developer story review workflow
  (instructions.xml, checklist.md, workflow.yaml).  Four-pass
  structure (AC validation / task audit / code quality / test
  quality) was adapted into the evolve round-review attack plan.

## Why archive instead of delete

Future iterations of the review system may want to borrow more
from the BMad originals — session-end review, sprint tracking,
sharded-artifact navigation — without reinventing the framing.
Keeping the originals verbatim lets a future refactor diff against
the source, rather than reconstructing the adaptation decisions.

## When to update

These files should change only when:

1. The upstream BMad project publishes a new version and the
   maintainer wants to sync — overwrite the file entirely and note
   the upstream version/commit in a commit message.
2. An evolve-native doc is being derived from one of these and the
   maintainer wants to diff — edit the evolve-native file in
   `tasks/`, not the archived original.

Never edit these files to reflect evolve-specific conventions; that
defeats the "verbatim reference" purpose.
