"""Shared pytest fixtures.

Clears Rich's Style.parse LRU cache between tests. Without this, other tests
(notably in test_agent_coverage.py that instantiate RichTUI through
run_claude_agent) leave the cache in a state where later Rich renders fail
with `AttributeError: 'Style' object has no attribute 'strip'` — the cache
hashes arguments by identity, and the test-polluted cache maps a valid Style
instance to a stale entry that routes through `Style.parse(a_Style_object)`
instead of the short-circuit `isinstance(name, Style)` path.

The fixture is autouse + function scope: every test starts with an empty
Style.parse cache, so the rendering path is deterministic regardless of
what ran before.
"""

import pytest


@pytest.fixture(autouse=True)
def _clear_rich_style_cache():
    try:
        from rich.style import Style
        Style.parse.cache_clear()
    except (ImportError, AttributeError):
        pass
    yield
    try:
        from rich.style import Style
        Style.parse.cache_clear()
    except (ImportError, AttributeError):
        pass
