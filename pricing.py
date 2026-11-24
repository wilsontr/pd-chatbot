"""One pricing table and the cost arithmetic, shared by production and evaluation.

The single table matters: the eval harness measures cost per query, and production
reports cost per request. If they used different rates or different token
conventions the two numbers would drift apart while looking comparable.

Token conventions differ by provider and the difference is easy to get wrong:

* **Anthropic** reports ``input_tokens`` *excluding* cached tokens;
  ``cache_read_input_tokens`` and ``cache_creation_input_tokens`` are separate,
  disjoint buckets.
* **DeepSeek** (OpenAI-compatible) reports ``prompt_tokens`` *including* cache
  hits; ``prompt_cache_hit_tokens`` is a subset of it.

``usage_buckets`` normalises both into disjoint buckets so cost arithmetic is
provider-agnostic. Subtracting cache reads from Anthropic's ``input_tokens``
(treating it like DeepSeek) would understate cost, which is what the harness did
before this module existed.

Prices are peak/list rates from the published sheets, synced 2026-11-23:
  Anthropic: https://platform.claude.com/docs/en/about-claude/pricing
  DeepSeek:  https://api-docs.deepseek.com/quick_start/pricing/
DeepSeek bills at half its peak rate off-peak, so its costs here are upper
bounds. An id that is missing from the table returns ``None`` rather than a
stale number: re-add an id only with a rate read off the sheet.
"""

from __future__ import annotations

from typing import Any, Mapping

PRICING_PER_MTOK: dict[str, dict[str, float]] = {
    # Anthropic (flat pricing, no peak/off-peak). cache_read is the discounted
    # rate for a cache hit; the write rate is derived from the input rate below.
    "claude-sonnet-4-6": {"input": 3.00, "cache_read": 0.30, "output": 15.00},
    "claude-sonnet-5-5": {"input": 2.00, "cache_read": 0.10, "output": 10.00},
    "claude-haiku-4-5": {"input": 1.00, "cache_read": 0.10, "output": 5.00},
    "claude-haiku-5-5": {"input": 0.10, "cache_read": 0.01, "output": 0.50},
    # DeepSeek (peak rates; half price off-peak)
    "deepseek-flash": {"input": 0.30, "cache_read": 0.006, "output": 1.20},
    "deepseek-v4-pro": {"input": 1.32, "cache_read": 0.044, "output": 3.96},
}

# Anthropic's 5-minute ephemeral cache writes are billed at 1.25x the input rate;
# the 1-hour cache is 2x. Every cache_control in this repo uses the 5-minute
# default, so a single multiplier is enough here.
ANTHROPIC_CACHE_WRITE_MULTIPLIER = 1.25


def provider_of(model: str) -> str:
    return "deepseek" if model.lower().startswith("deepseek") else "anthropic"


def _token(usage: Mapping[str, int], *names: str) -> int:
    """First present token count under any of ``names``.

    Two spellings exist in this codebase and both must keep working:
    Langfuse's ``usage_details`` style (``input``/``output``/``cache_read``, used by
    rag._token_usage) and the eval harness's explicit style
    (``input_tokens``/``output_tokens``/``cache_read_tokens``). Silently reading
    the wrong one would price every call at zero.
    """
    for name in names:
        value = usage.get(name)
        if value is not None:
            return int(value)
    return 0


def usage_buckets(model: str, usage: Mapping[str, int]) -> dict[str, int]:
    """Disjoint token buckets for a model call.

    Returns ``uncached_input``, ``cache_write``, ``cache_read`` and ``output``.
    Unknown/absent fields default to zero, so a partial usage dict still works.
    """
    raw_input = _token(usage, "input_tokens", "input")
    cache_read = _token(usage, "cache_read_tokens", "cache_read")
    cache_write = _token(usage, "cache_write_tokens", "cache_write")
    output = _token(usage, "output_tokens", "output")

    if provider_of(model) == "deepseek":
        # prompt_tokens includes cache reads: subtract them to get the uncached part.
        uncached_input = max(0, raw_input - cache_read)
        cache_write = 0  # DeepSeek has no separate cache-write charge
    else:
        # Anthropic's input_tokens is already uncached-only.
        uncached_input = raw_input
    return {
        "uncached_input": uncached_input,
        "cache_write": cache_write,
        "cache_read": cache_read,
        "output": output,
    }


def _rates(model: str) -> dict[str, float] | None:
    return PRICING_PER_MTOK.get(model)


def cost_usd(model: str, usage: Mapping[str, int]) -> float | None:
    """Actual cost of one call in USD, or None if the model has no known rate."""
    rates = _rates(model)
    if rates is None:
        return None
    buckets = usage_buckets(model, usage)
    write_rate = rates["input"] * (
        ANTHROPIC_CACHE_WRITE_MULTIPLIER if provider_of(model) == "anthropic" else 1.0
    )
    return (
        buckets["uncached_input"] * rates["input"]
        + buckets["cache_write"] * write_rate
        + buckets["cache_read"] * rates["cache_read"]
        + buckets["output"] * rates["output"]
    ) / 1_000_000


def cost_without_cache_usd(model: str, usage: Mapping[str, int]) -> float | None:
    """What the same call would have cost with caching disabled.

    Every input token is billed at the full input rate; there is no write
    premium because nothing is written to the cache.
    """
    rates = _rates(model)
    if rates is None:
        return None
    buckets = usage_buckets(model, usage)
    input_tokens = buckets["uncached_input"] + buckets["cache_write"] + buckets["cache_read"]
    return (
        input_tokens * rates["input"] + buckets["output"] * rates["output"]
    ) / 1_000_000


def cache_savings_usd(model: str, usage: Mapping[str, int]) -> float | None:
    """Dollars saved by prompt caching for one call (can be negative).

    Negative when the call only *wrote* to the cache (the write premium is paid
    before any read benefit), which is why per-request reporting sums real calls
    rather than assuming every request wins.
    """
    actual = cost_usd(model, usage)
    without = cost_without_cache_usd(model, usage)
    if actual is None or without is None:
        return None
    return without - actual
