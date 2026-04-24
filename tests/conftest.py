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

import sys
from unittest.mock import MagicMock

import pytest


# Install a minimal ``claude_agent_sdk`` stub when the real SDK is not
# importable.  Several tests mock high-level entry points like
# ``agent.run_claude_agent`` to exercise orchestration logic, but
# ``agent._run_agent_with_retries`` does ``from claude_agent_sdk import
# query`` as an availability check and returns early on ImportError —
# which bypasses the test's mock.  Seeding ``sys.modules`` satisfies the
# import check without requiring the real package; it is a no-op when
# the SDK is installed (CI, venv-activated dev sessions) and harmless
# to tests that intentionally re-simulate ImportError via
# ``patch.dict('sys.modules', {'claude_agent_sdk': None})``.
if "claude_agent_sdk" not in sys.modules:
    try:
        import claude_agent_sdk  # noqa: F401
    except ImportError:
        _sdk_stub = MagicMock()
        _sdk_stub._internal = MagicMock()
        sys.modules["claude_agent_sdk"] = _sdk_stub
        sys.modules["claude_agent_sdk._internal"] = _sdk_stub._internal


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
