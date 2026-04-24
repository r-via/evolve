"""Tests for costs.py — token tracking, cost estimation, budget enforcement."""

import json
from pathlib import Path

import pytest

from evolve.costs import (
    RATES,
    TokenUsage,
    aggregate_usage,
    build_usage_state,
    estimate_cost,
    format_cost,
)


# ---------------------------------------------------------------------------
# TokenUsage dataclass
# ---------------------------------------------------------------------------

class TestTokenUsage:
    def test_defaults(self):
        u = TokenUsage()
        assert u.input_tokens == 0
        assert u.output_tokens == 0
        assert u.cache_creation_tokens == 0
        assert u.cache_read_tokens == 0
        assert u.round is None
        assert u.model is None
        assert u.timestamp is None

    def test_addition(self):
        a = TokenUsage(input_tokens=100, output_tokens=200, cache_creation_tokens=50, cache_read_tokens=300)
        b = TokenUsage(input_tokens=400, output_tokens=500, cache_creation_tokens=60, cache_read_tokens=700)
        result = a + b
        assert result.input_tokens == 500
        assert result.output_tokens == 700
        assert result.cache_creation_tokens == 110
        assert result.cache_read_tokens == 1000
        # Addition does not carry round/model/timestamp
        assert result.round is None
        assert result.model is None

    def test_addition_not_implemented(self):
        u = TokenUsage()
        result = u.__add__(42)
        assert result is NotImplemented

    def test_iadd(self):
        total = TokenUsage(input_tokens=100, output_tokens=200)
        r = TokenUsage(input_tokens=50, output_tokens=75, cache_creation_tokens=10, cache_read_tokens=20)
        total += r
        assert total.input_tokens == 150
        assert total.output_tokens == 275
        assert total.cache_creation_tokens == 10
        assert total.cache_read_tokens == 20

    def test_iadd_not_implemented(self):
        u = TokenUsage()
        result = u.__iadd__("bad")
        assert result is NotImplemented

    def test_to_dict_minimal(self):
        u = TokenUsage(input_tokens=10, output_tokens=20)
        d = u.to_dict()
        assert d == {
            "input_tokens": 10,
            "output_tokens": 20,
            "cache_creation_tokens": 0,
            "cache_read_tokens": 0,
        }
        assert "round" not in d
        assert "model" not in d
        assert "timestamp" not in d

    def test_to_dict_full(self):
        u = TokenUsage(
            input_tokens=45230,
            output_tokens=12400,
            cache_creation_tokens=8200,
            cache_read_tokens=38100,
            round=3,
            model="claude-opus-4-6",
            timestamp="2026-04-24T16:02:01Z",
        )
        d = u.to_dict()
        assert d["round"] == 3
        assert d["model"] == "claude-opus-4-6"
        assert d["timestamp"] == "2026-04-24T16:02:01Z"
        assert d["input_tokens"] == 45230

    def test_from_dict(self):
        data = {
            "input_tokens": 1000,
            "output_tokens": 2000,
            "cache_creation_tokens": 300,
            "cache_read_tokens": 400,
            "round": 1,
            "model": "claude-opus-4-6",
            "timestamp": "2026-01-01T00:00:00Z",
        }
        u = TokenUsage.from_dict(data)
        assert u.input_tokens == 1000
        assert u.output_tokens == 2000
        assert u.cache_creation_tokens == 300
        assert u.cache_read_tokens == 400
        assert u.round == 1
        assert u.model == "claude-opus-4-6"
        assert u.timestamp == "2026-01-01T00:00:00Z"

    def test_from_dict_missing_keys(self):
        """Missing keys default to 0/None."""
        u = TokenUsage.from_dict({})
        assert u.input_tokens == 0
        assert u.output_tokens == 0
        assert u.round is None

    def test_roundtrip_dict(self):
        u = TokenUsage(input_tokens=100, output_tokens=200, round=5, model="m")
        assert TokenUsage.from_dict(u.to_dict()).input_tokens == 100

    def test_save_and_from_file(self, tmp_path):
        path = tmp_path / "usage_round_1.json"
        u = TokenUsage(
            input_tokens=45230,
            output_tokens=12400,
            cache_creation_tokens=8200,
            cache_read_tokens=38100,
            round=1,
            model="claude-opus-4-6",
            timestamp="2026-04-24T16:02:01Z",
        )
        u.save(path)
        loaded = TokenUsage.from_file(path)
        assert loaded.input_tokens == 45230
        assert loaded.output_tokens == 12400
        assert loaded.model == "claude-opus-4-6"
        assert loaded.round == 1

    def test_save_creates_valid_json(self, tmp_path):
        path = tmp_path / "test.json"
        TokenUsage(input_tokens=1).save(path)
        data = json.loads(path.read_text())
        assert data["input_tokens"] == 1


# ---------------------------------------------------------------------------
# RATES table
# ---------------------------------------------------------------------------

class TestRates:
    def test_opus_rates(self):
        assert "claude-opus-4-6" in RATES
        r = RATES["claude-opus-4-6"]
        assert r["input"] == 15.0
        assert r["output"] == 75.0
        assert r["cache_read"] == 1.5

    def test_sonnet_rates(self):
        assert "claude-sonnet-4-20250514" in RATES
        r = RATES["claude-sonnet-4-20250514"]
        assert r["input"] == 3.0
        assert r["output"] == 15.0
        assert r["cache_read"] == 0.3


# ---------------------------------------------------------------------------
# estimate_cost
# ---------------------------------------------------------------------------

class TestEstimateCost:
    def test_opus_cost(self):
        u = TokenUsage(input_tokens=45230, output_tokens=12400, cache_read_tokens=38100)
        cost = estimate_cost(u, "claude-opus-4-6")
        assert cost is not None
        # input: 45230 * 15.0 / 1M = 0.67845
        # output: 12400 * 75.0 / 1M = 0.93
        # cache: 38100 * 1.5 / 1M = 0.05715
        expected = round((45230 * 15.0 + 12400 * 75.0 + 38100 * 1.5) / 1_000_000, 4)
        assert cost == expected

    def test_sonnet_cost(self):
        u = TokenUsage(input_tokens=100000, output_tokens=50000, cache_read_tokens=80000)
        cost = estimate_cost(u, "claude-sonnet-4-20250514")
        assert cost is not None
        expected = round((100000 * 3.0 + 50000 * 15.0 + 80000 * 0.3) / 1_000_000, 4)
        assert cost == expected

    def test_unknown_model_returns_none(self):
        u = TokenUsage(input_tokens=1000, output_tokens=500)
        assert estimate_cost(u, "unknown-model-xyz") is None

    def test_custom_rates_override(self):
        u = TokenUsage(input_tokens=1_000_000, output_tokens=0)
        custom = {"input": 10.0, "output": 50.0, "cache_read": 1.0}
        cost = estimate_cost(u, "any-model", custom_rates=custom)
        assert cost == 10.0  # 1M * 10.0 / 1M

    def test_custom_rates_for_unknown_model(self):
        """Custom rates work even when the model isn't in the built-in table."""
        u = TokenUsage(input_tokens=500_000, output_tokens=100_000)
        custom = {"input": 20.0, "output": 100.0}
        cost = estimate_cost(u, "my-custom-model", custom_rates=custom)
        assert cost is not None
        expected = round((500_000 * 20.0 + 100_000 * 100.0) / 1_000_000, 4)
        assert cost == expected

    def test_zero_tokens(self):
        u = TokenUsage()
        cost = estimate_cost(u, "claude-opus-4-6")
        assert cost == 0.0

    def test_only_cache_read(self):
        u = TokenUsage(cache_read_tokens=1_000_000)
        cost = estimate_cost(u, "claude-opus-4-6")
        assert cost == 1.5  # 1M * 1.5 / 1M


# ---------------------------------------------------------------------------
# format_cost
# ---------------------------------------------------------------------------

class TestFormatCost:
    def test_known_cost(self):
        assert format_cost(12.40) == "$12.40"

    def test_small_cost(self):
        assert format_cost(0.05) == "$0.05"

    def test_zero(self):
        assert format_cost(0.0) == "$0.00"

    def test_none(self):
        assert format_cost(None) == "unknown"

    def test_large_cost(self):
        assert format_cost(123.456) == "$123.46"


# ---------------------------------------------------------------------------
# aggregate_usage
# ---------------------------------------------------------------------------

class TestAggregateUsage:
    def test_basic_aggregation(self, tmp_path):
        # Write two usage files
        r1 = TokenUsage(input_tokens=1000, output_tokens=500, round=1, model="claude-opus-4-6")
        r2 = TokenUsage(input_tokens=2000, output_tokens=1000, cache_read_tokens=500, round=2, model="claude-opus-4-6")
        r1.save(tmp_path / "usage_round_1.json")
        r2.save(tmp_path / "usage_round_2.json")

        total, cost, tracked = aggregate_usage(tmp_path, 2)
        assert total.input_tokens == 3000
        assert total.output_tokens == 1500
        assert total.cache_read_tokens == 500
        assert tracked == 2
        assert cost is not None
        assert cost > 0

    def test_missing_files_skipped(self, tmp_path):
        r1 = TokenUsage(input_tokens=100, output_tokens=50, round=1, model="claude-opus-4-6")
        r1.save(tmp_path / "usage_round_1.json")
        # usage_round_2.json is missing

        total, cost, tracked = aggregate_usage(tmp_path, 3)
        assert total.input_tokens == 100
        assert tracked == 1

    def test_corrupt_file_skipped(self, tmp_path):
        r1 = TokenUsage(input_tokens=100, output_tokens=50, round=1, model="claude-opus-4-6")
        r1.save(tmp_path / "usage_round_1.json")
        (tmp_path / "usage_round_2.json").write_text("NOT JSON")

        total, cost, tracked = aggregate_usage(tmp_path, 2)
        assert total.input_tokens == 100
        assert tracked == 1

    def test_no_files(self, tmp_path):
        total, cost, tracked = aggregate_usage(tmp_path, 5)
        assert total.input_tokens == 0
        assert cost is None
        assert tracked == 0

    def test_unknown_model(self, tmp_path):
        r1 = TokenUsage(input_tokens=100, output_tokens=50, round=1, model="unknown-model")
        r1.save(tmp_path / "usage_round_1.json")

        total, cost, tracked = aggregate_usage(tmp_path, 1)
        assert total.input_tokens == 100
        assert cost is None  # unknown model
        assert tracked == 1

    def test_no_model_field(self, tmp_path):
        """Usage files without model field -> cost is None."""
        r1 = TokenUsage(input_tokens=100, output_tokens=50, round=1)
        r1.save(tmp_path / "usage_round_1.json")

        total, cost, tracked = aggregate_usage(tmp_path, 1)
        assert total.input_tokens == 100
        assert cost is None
        assert tracked == 1


# ---------------------------------------------------------------------------
# build_usage_state
# ---------------------------------------------------------------------------

class TestBuildUsageState:
    def test_known_cost(self):
        total = TokenUsage(
            input_tokens=234500,
            output_tokens=87200,
            cache_creation_tokens=42000,
            cache_read_tokens=189000,
        )
        state = build_usage_state(total, 12.40, 8)
        assert state == {
            "total_input_tokens": 234500,
            "total_output_tokens": 87200,
            "total_cache_creation_tokens": 42000,
            "total_cache_read_tokens": 189000,
            "estimated_cost_usd": 12.40,
            "rounds_tracked": 8,
        }

    def test_unknown_cost(self):
        total = TokenUsage(input_tokens=100)
        state = build_usage_state(total, None, 1)
        assert state["estimated_cost_usd"] == "unknown"
        assert state["rounds_tracked"] == 1

    def test_zero_state(self):
        total = TokenUsage()
        state = build_usage_state(total, 0.0, 0)
        assert state["total_input_tokens"] == 0
        assert state["estimated_cost_usd"] == 0.0
        assert state["rounds_tracked"] == 0
