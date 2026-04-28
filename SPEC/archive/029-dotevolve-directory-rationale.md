# `.evolve/` Directory Rationale

> Archived from SPEC.md § "The `.evolve/` directory" — design
> rationale for the dotfile convention, decision locked.

When evolve is used as a pip-installed module driving a third-party
project (`python -m evolve start <target-project>`), the target
project is NOT evolve's own repository — it's arbitrary user code.
Dropping a top-level `runs/` directory into that project pollutes its
root with a non-idiomatic name that clashes with the target's own
conventions and shows up in every `ls`, every `git status`, every IDE
file tree.  The `.evolve/` prefix makes it immediately obvious the
directory is tool-managed state, follows the universal dotfile
convention every developer already knows, and is easy to gitignore (a
single `.evolve/` line) for projects that treat evolution artifacts as
local-only.
