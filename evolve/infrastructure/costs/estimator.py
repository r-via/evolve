"""Token tracking, cost estimation, and budget enforcement.

Provides the TokenUsage dataclass for per-round token counts and the
estimate_cost function for converting token counts to estimated USD.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# Built-in rates per 1M tokens, updated periodically.
RATES: dict[str, dict[str, float]] = {
    "claude-opus-4-6": {
        "input": 15.0,
        "output": 75.0,
        "cache_read": 1.5,
    },
    "claude-sonnet-4-20250514": {
        "input": 3.0,
        "output": 15.0,
        "cache_read": 0.3,
    },
}


@dataclass
class TokenUsage:
    """Per-round token usage from the Claude Agent SDK response.

    Supports addition so per-round usage can be accumulated into session
    totals via ``total = round1 + round2 + ...``.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    round: Optional[int] = None
    model: Optional[str] = None
    timestamp: Optional[str] = None

    def __add__(self, other: TokenUsage) -> TokenUsage:
        if not isinstance(other, TokenUsage):
            return NotImplemented
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_creation_tokens=self.cache_creation_tokens + other.cache_creation_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
        )

    def __iadd__(self, other: TokenUsage) -> TokenUsage:
        if not isinstance(other, TokenUsage):
            return NotImplemented
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_creation_tokens += other.cache_creation_tokens
        self.cache_read_tokens += other.cache_read_tokens
        return self

    def to_dict(self) -> dict:
        """Serialize to a dict suitable for JSON (usage_round_N.json)."""
        d: dict = {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "cache_read_tokens": self.cache_read_tokens,
        }
        if self.round is not None:
            d["round"] = self.round
        if self.model is not None:
            d["model"] = self.model
        if self.timestamp is not None:
            d["timestamp"] = self.timestamp
        return d

    @classmethod
    def from_dict(cls, data: dict) -> TokenUsage:
        """Deserialize from a dict (e.g. parsed usage_round_N.json)."""
        return cls(
            input_tokens=data.get("input_tokens", 0),
            output_tokens=data.get("output_tokens", 0),
            cache_creation_tokens=data.get("cache_creation_tokens", 0),
            cache_read_tokens=data.get("cache_read_tokens", 0),
            round=data.get("round"),
            model=data.get("model"),
            timestamp=data.get("timestamp"),
        )

    @classmethod
    def from_file(cls, path: Path) -> TokenUsage:
        """Load a TokenUsage from a usage_round_N.json file."""
        with open(path) as f:
            return cls.from_dict(json.load(f))

    def save(self, path: Path) -> None:
        """Write this usage to a JSON file."""
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)
            f.write("\n")


def estimate_cost(
    usage: TokenUsage,
    model: str,
    custom_rates: Optional[dict[str, float]] = None,
) -> Optional[float]:
    """Estimate cost in USD for the given token usage.

    Uses custom_rates if provided, otherwise falls back to the built-in
    RATES table.  Returns None when the model is not in either table --
    token counts are still tracked but cost is "unknown".

    Rates are per 1M tokens.
    """
    rates = custom_rates if custom_rates is not None else RATES.get(model)
    if rates is None:
        return None

    input_rate = rates.get("input", 0.0)
    output_rate = rates.get("output", 0.0)
    cache_read_rate = rates.get("cache_read", 0.0)

    cost = (
        usage.input_tokens * input_rate
        + usage.output_tokens * output_rate
        + usage.cache_read_tokens * cache_read_rate
    ) / 1_000_000

    return round(cost, 4)


def format_cost(cost: Optional[float]) -> str:
    """Format a cost value for display.

    Returns "$X.XX" for known costs, "unknown" for None.
    """
    if cost is None:
        return "unknown"
    return f"${cost:.2f}"


def aggregate_usage(run_dir: Path, rounds_completed: int) -> tuple[TokenUsage, Optional[float], int]:
    """Aggregate token usage across all rounds in a session.

    Scans ``run_dir`` for ``usage_round_N.json`` files (N=1..rounds_completed),
    accumulates token counts, and estimates total cost.

    Returns:
        (total_usage, estimated_cost_usd, rounds_tracked)
        estimated_cost_usd is None when the model is unknown.
    """
    total = TokenUsage()
    rounds_tracked = 0
    model: Optional[str] = None

    for n in range(1, rounds_completed + 1):
        usage_path = run_dir / f"usage_round_{n}.json"
        if usage_path.exists():
            try:
                round_usage = TokenUsage.from_file(usage_path)
                total += round_usage
                rounds_tracked += 1
                if round_usage.model:
                    model = round_usage.model
            except (json.JSONDecodeError, KeyError, OSError):
                continue

    cost = estimate_cost(total, model) if model else None
    return total, cost, rounds_tracked


def build_usage_state(
    total: TokenUsage,
    estimated_cost: Optional[float],
    rounds_tracked: int,
) -> dict:
    """Build the ``usage`` object for state.json.

    Matches the schema documented in SPEC.md.
    """
    return {
        "total_input_tokens": total.input_tokens,
        "total_output_tokens": total.output_tokens,
        "total_cache_creation_tokens": total.cache_creation_tokens,
        "total_cache_read_tokens": total.cache_read_tokens,
        "estimated_cost_usd": estimated_cost if estimated_cost is not None else "unknown",
        "rounds_tracked": rounds_tracked,
    }
