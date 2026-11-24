"""Tests for the shared pricing table and cost arithmetic (TASK-4).

The provider distinction is the important part: Anthropic reports input_tokens
*excluding* cached tokens, DeepSeek reports prompt_tokens *including* cache hits.
Getting that backwards silently understates cost.
"""

import pytest

import pricing


class TestBuckets:
    def test_anthropic_input_excludes_cache_tokens(self):
        buckets = pricing.usage_buckets("claude-haiku-5-5", {
            "input_tokens": 500,          # uncached only
            "cache_read_tokens": 800,
            "cache_write_tokens": 0,
            "output_tokens": 300,
        })
        assert buckets == {
            "uncached_input": 500, "cache_write": 0,
            "cache_read": 800, "output": 300,
        }

    def test_deepseek_input_includes_cache_hits(self):
        buckets = pricing.usage_buckets("deepseek-flash", {
            "input_tokens": 1300,         # includes the 800 cached
            "cache_read_tokens": 800,
            "output_tokens": 300,
        })
        assert buckets == {
            "uncached_input": 500, "cache_write": 0,
            "cache_read": 800, "output": 300,
        }

    def test_langfuse_style_keys_are_understood(self):
        # rag._token_usage emits Langfuse's spelling; a silent mismatch here
        # priced every production call at zero the first time round.
        buckets = pricing.usage_buckets("claude-haiku-5-5", {
            "input": 500, "output": 300, "cache_read": 800, "cache_write": 0,
        })
        assert buckets["uncached_input"] == 500
        assert buckets["cache_read"] == 800
        assert buckets["output"] == 300

    def test_both_key_styles_price_the_same(self):
        langfuse_style = {"input": 500, "output": 300, "cache_read": 800}
        eval_style = {"input_tokens": 500, "output_tokens": 300, "cache_read_tokens": 800}
        assert pricing.cost_usd("claude-haiku-5-5", langfuse_style) == \
            pricing.cost_usd("claude-haiku-5-5", eval_style)

    def test_missing_fields_default_to_zero(self):
        assert pricing.usage_buckets("claude-haiku-5-5", {}) == {
            "uncached_input": 0, "cache_write": 0, "cache_read": 0, "output": 0,
        }


class TestCost:
    def test_uncached_cost_is_linear(self):
        # 1M input + 1M output at haiku-5-5 rates = $0.10 + $0.50
        cost = pricing.cost_usd("claude-haiku-5-5", {
            "input_tokens": 1_000_000, "output_tokens": 1_000_000,
        })
        assert cost == pytest.approx(0.60)

    def test_anthropic_cache_read_is_discounted(self):
        usage = {"input_tokens": 0, "cache_read_tokens": 1_000_000, "output_tokens": 0}
        assert pricing.cost_usd("claude-haiku-5-5", usage) == pytest.approx(0.01)

    def test_anthropic_cache_write_carries_a_premium(self):
        # 1M written tokens at 1.25x the $0.10 input rate
        usage = {"input_tokens": 0, "cache_write_tokens": 1_000_000, "output_tokens": 0}
        assert pricing.cost_usd("claude-haiku-5-5", usage) == pytest.approx(0.125)

    def test_deepseek_has_no_write_premium(self):
        usage = {"input_tokens": 1_000_000, "cache_write_tokens": 1_000_000,
                 "output_tokens": 0}
        buckets = pricing.usage_buckets("deepseek-flash", usage)
        assert buckets["cache_write"] == 0
        assert pricing.cost_usd("deepseek-flash", usage) == pytest.approx(0.30)

    def test_unknown_model_costs_nothing_known(self):
        assert pricing.cost_usd("some-new-model", {"input_tokens": 10}) is None
        assert pricing.cache_savings_usd("some-new-model", {"input_tokens": 10}) is None


class TestCacheSavings:
    def test_reads_save_the_rate_difference(self):
        usage = {"input_tokens": 0, "cache_read_tokens": 1_000_000, "output_tokens": 0}
        # uncached would be $0.10, cached is $0.01
        assert pricing.cache_savings_usd("claude-haiku-5-5", usage) == pytest.approx(0.09)

    def test_a_pure_write_request_loses_money(self):
        usage = {"input_tokens": 0, "cache_write_tokens": 1_000_000, "output_tokens": 0}
        assert pricing.cache_savings_usd("claude-haiku-5-5", usage) == pytest.approx(-0.025)

    def test_actual_plus_savings_equals_uncached_cost(self):
        usage = {"input_tokens": 700, "cache_read_tokens": 800,
                 "cache_write_tokens": 100, "output_tokens": 250}
        actual = pricing.cost_usd("claude-haiku-5-5", usage)
        without = pricing.cost_without_cache_usd("claude-haiku-5-5", usage)
        savings = pricing.cache_savings_usd("claude-haiku-5-5", usage)
        assert actual + savings == pytest.approx(without)

    def test_system_prompt_cache_savings_are_small_but_positive(self):
        # The cached prefix is the ~800-token system prompt, not the excerpts.
        usage = {"input_tokens": 3000, "cache_read_tokens": 800, "output_tokens": 800}
        assert 0 < pricing.cache_savings_usd("claude-haiku-5-5", usage) < 0.0005
