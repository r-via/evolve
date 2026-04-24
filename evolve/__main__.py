"""Entry point for ``python -m evolve``.

Delegates to the CLI module's ``main`` function so the orchestrator can spawn
round subprocesses via ``python -m evolve _round ...`` without needing a
root-level ``evolve.py`` file.
"""
from evolve.cli import main

if __name__ == "__main__":
    main()
