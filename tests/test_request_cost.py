"""Tests for per-request cost accounting (TASK-4)."""

import pytest

import cost_tracker


class TestRequestCost:
    def test_records_calls_and_sums_cost(self):
        tracker = cost_tracker.RequestCost()
        tracker.record("classify", "claude-haiku-4-5",
                       {"input_tokens": 500, "output_tokens": 50})
        tracker.record("generate", "claude-haiku-5-5",
                       {"input_tokens": 4000, "output_tokens": 800})
        summary = tracker.summary()
        assert summary["calls"] == 2
        assert summary["total_usd"] > 0
        assert summary["by_kind"]["generate"]["calls"] == 1
        assert summary["by_kind"]["classify"]["calls"] == 1

    def test_unpriced_models_are_reported_not_silently_zero(self):
        tracker = cost_tracker.RequestCost()
        tracker.record("generate", "mystery-model", {"input_tokens": 1000})
        summary = tracker.summary()
        assert summary["unpriced_models"] == ["mystery-model"]
        assert summary["priced_models"] == []
        assert summary["total_usd"] == 0.0

    def test_empty_usage_is_ignored(self):
        tracker = cost_tracker.RequestCost()
        tracker.record("generate", "claude-haiku-5-5", None)
        tracker.record("generate", "claude-haiku-5-5", {})
        assert tracker.summary()["calls"] == 0

    def test_cache_savings_are_summed(self):
        tracker = cost_tracker.RequestCost()
        tracker.record("generate", "claude-haiku-5-5",
                       {"input_tokens": 100, "cache_read_tokens": 800, "output_tokens": 100})
        assert tracker.summary()["cache_savings_usd"] > 0


class TestContextTracking:
    def test_record_outside_a_request_is_a_no_op(self):
        cost_tracker.finish(None)  # clear any leaked state
        cost_tracker.record("generate", "claude-haiku-5-5", {"input_tokens": 10})
        assert cost_tracker.current() is None

    def test_start_record_finish(self):
        token = cost_tracker.start()
        cost_tracker.record("classify", "claude-haiku-4-5",
                            {"input_tokens": 100, "output_tokens": 10})
        assert cost_tracker.current() is not None
        summary = cost_tracker.finish(token)
        assert summary is not None
        assert summary["calls"] == 1
        assert cost_tracker.current() is None

    def test_finish_without_a_request_returns_none(self):
        cost_tracker.finish(None)
        assert cost_tracker.finish(None) is None

    def test_two_sequential_requests_do_not_mix(self):
        token = cost_tracker.start()
        cost_tracker.record("generate", "claude-haiku-5-5", {"input_tokens": 100})
        first = cost_tracker.finish(token)

        token = cost_tracker.start()
        cost_tracker.record("generate", "claude-haiku-5-5", {"input_tokens": 100})
        cost_tracker.record("generate", "claude-haiku-5-5", {"input_tokens": 100})
        second = cost_tracker.finish(token)

        assert first["calls"] == 1
        assert second["calls"] == 2
