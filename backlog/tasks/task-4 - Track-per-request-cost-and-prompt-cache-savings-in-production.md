---
id: TASK-4
title: Track per-request cost and prompt-cache savings in production
status: Done
assignee: []
created_date: '2026-11-23 20:39'
updated_date: '2026-11-24 04:14'
labels:
  - observability
dependencies: []
references:
  - rag.py
  - evaluation/generation.py
priority: low
type: enhancement
ordinal: 4000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Langfuse traces in `rag.py` already record token usage (including cache reads), and `evaluation/generation.py` has a `_cost_usd` helper, but production requests do not report dollar cost and nobody can see how much prompt caching saves. Quantifying that is a core LLM-ops story.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Each production trace records the cost in USD for every model call (classifier, history compression, generation, guardrail checks) and a total for the request
- [x] #2 Cache-read savings are calculated per request (actual cost vs. cost without caching)
- [x] #3 One pricing table is shared by production and the eval harness
- [x] #4 The README reports observed average cost per request and cache savings
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented 2026-11-24.

- pricing.py is the single pricing table, imported by both rag.py (production) and evaluation/generation.py (harness). It also fixes a real accounting bug: Anthropic's input_tokens excludes cache reads/writes while DeepSeek's prompt_tokens includes cache hits, so the previous formula (input - cache_read) understated Anthropic cost whenever the prompt cache was warm. usage_buckets() normalises both providers into disjoint buckets, and it accepts both token-key spellings in the repo (Langfuse's input/output/cache_read and the harness's input_tokens/output_tokens/cache_read_tokens) after the first measurement showed every production call priced at zero from a key mismatch.
- Cache accounting includes the 1.25x Anthropic 5-minute cache-write premium, so a request that only writes to the cache reports negative savings and a read-heavy one reports positive savings; cache_savings_usd = cost_without_cache - cost.
- cost_tracker.py accumulates the model calls of one request through a ContextVar (FastAPI tasks are isolated; asyncio.to_thread copies the context but shares the tracker object, so guard calls still record). rag.py starts tracking in both entry points (chat and async_stream_chat), records classify / generate / history compression / faithfulness guard with the model that served each call, attaches cost_details to each Langfuse observation, and on finish writes the total plus per-kind breakdown into the chat trace metadata and records Custom/Cost/RequestUsd and Custom/Cost/CacheSavingsUsd in New Relic. The guard got its own priced observation, which it previously lacked.
- A fix caught by the guardrails test: an early version put 'return None' in the finally block of _check_faithfulness, silently discarding the judge result. Removed; test_guardrails passes.

Measured over five live streamed requests (2026-11-24, current models): average $0.004863/request ($4.86 per 1000), split ~74% faithfulness guard, ~16% generation, ~10% classification; prompt-cache savings $0.000094/request (~1.9%). A second three-request run gave $0.004689 and $0.000095. The guard dominance is the notable finding: it re-reads the excerpts, so its input is several times the answer's. Only the ~800-token system prompt is cacheable (the excerpts vary per query), which caps cache savings at ~2% of request cost.

Verification: tests/test_pricing.py (14) and tests/test_request_cost.py (6) cover bucket normalisation for both providers, exact cost arithmetic, the cache-write premium, cache-savings identity (actual + savings == uncached cost), unpriced models being reported rather than zeroed, tracker accumulation/isolation, and no-op recording outside a request. Full suite 132 passed. README has a Cost per request section with the measured table.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Production now prices every request instead of only counting tokens.

pricing.py is the single rate table, imported by production and by the evaluation harness, so a query's eval cost and a request's production cost are directly comparable. Building it surfaced an accounting bug: Anthropic's input_tokens excludes cached tokens while DeepSeek's prompt_tokens includes them, so the old 'input minus cache_read' formula understated cost whenever the prompt cache was warm. usage_buckets() normalises both providers (and both token-key spellings already present in the repo) into disjoint buckets, and cache savings are computed as uncached-cost minus actual cost, including the 1.25x Anthropic cache-write premium.

cost_tracker.py accumulates the model calls of a single request through a context variable: classification, generation, history compression and the inline faithfulness guard, each priced with the model that served it. rag.py writes the per-call cost onto each Langfuse observation, adds the request total and per-kind breakdown to the chat trace, and records Custom/Cost/RequestUsd and Custom/Cost/CacheSavingsUsd in New Relic. The faithfulness guard, which previously had no observation of its own, is now priced explicitly.

Measured over five live streamed requests: $0.0048 per request ($4.80 per 1000), split roughly 74% faithfulness guard, 16% generation and 10% classification, with prompt-cache savings of $0.000095 per request (~2%). The guard dominates because it re-reads the retrieved excerpts; cache savings are capped because only the ~800-token system prompt is cacheable. Both facts, and the levers they imply, are documented in the README's Cost per request section.

Verified by 20 new unit tests (provider bucket normalisation, exact cost arithmetic, cache-write premium, savings identity, tracker isolation and no-op behaviour), a 132-test full suite, and two live request measurements. Commit cb484dd, local only.
<!-- SECTION:FINAL_SUMMARY:END -->
