# Model comparison — 2026-11-23

Which model should answer Pd questions? This records the comparison behind that
decision: five models, all 30 golden entries, 2 trials each, judged blind to
model identity by `claude-haiku-4-5` on the 1–5 rubric in
`evaluation/judge.py`. Raw per-entry scores are in
[`model_comparison_2026-11-23.json`](model_comparison_2026-11-23.json).

## Method

```bash
python -m evaluation.evaluate --generation --sample 30 --trials 2 \
  --model claude-sonnet-4-6 \
  --compare claude-sonnet-5-5 claude-haiku-5-5 deepseek-flash deepseek-v4-pro \
  --judge claude-haiku-4-5 \
  --output docs/model_comparison_2026-11-23.json
```

- **Sample**: all 30 entries (11 object-reference, 12 conceptual, 7 mixed; 3 hard).
  The CI gate's `--sample 10` prefix is all easy object-reference questions, which
  is fine for a regression gate and too narrow for a model decision.
- **Trials**: 2, reported as mean ± std across trials (`--trials`).
- **Prices**: peak rates synced 2026-11-23 from
  [Anthropic](https://platform.claude.com/docs/en/about-claude/pricing) and
  [DeepSeek](https://api-docs.deepseek.com/quick_start/pricing/); DeepSeek
  off-peak bills at half, so its costs here are upper bounds.
  `eval/generation.py::PRICING_PER_MTOK` holds the table.
- **Cost basis**: production runs **one** inline faithfulness judge per answer
  (`rag.py::_check_faithfulness`), so `prod $/query` below is generation +
  one third of the eval's three-judge cost. All dollars are measured token usage,
  not estimates.

## Quality

| Model | Faithfulness | Relevance | Citation | Output tokens/answer |
|---|---|---|---|---|
| `claude-sonnet-4-6` (current) | 4.80 ± 0.00 | 4.87 ± 0.00 | **4.65** ± 0.03 | 906 |
| `claude-sonnet-5-5` | 4.79 ± 0.02 | **4.92** ± 0.07 | 4.37 ± 0.09 | 1 598 |
| `claude-haiku-5-5` | 4.85 ± 0.07 | 4.90 ± 0.10 | 4.48 ± 0.07 | 1 884 |
| `deepseek-flash` | 4.87 ± 0.09 | 4.82 ± 0.07 | 4.37 ± 0.19 | 2 118 |
| `deepseek-v4-pro` | **4.99** ± 0.02 | 4.85 ± 0.03 | 4.51 ± 0.16 | 3 643 |

**On the two gated dimensions every model is inside the noise band.** The largest
faithfulness spread is 0.20 (4.79–4.99) against a trial-to-trial std of up to
0.09, and relevance spans 0.10 (4.82–4.92) with std up to 0.10. Nothing here
justifies preferring one model for answer quality.

**Citation correctness is the one real difference.** Sonnet 4.6 is ahead of every
other model (4.65 vs 4.37–4.51), consistently across both trials (4.63/4.67 for
4.6 versus 4.53/4.43 for Haiku 5.5), and the gap exceeds the trial std. Citation
is not gated in CI — the same sample produces a minimum of one 2 for every model,
because the judge penalises missing or unverifiable URLs — but it is the thing
this product is for, so treat it as a real cost of switching, not a rounding error.

## Cost

| Model | Generation $/query | Production $/1 000 queries | vs current |
|---|---|---|---|
| `claude-sonnet-4-6` (current) | $0.0241 | **$27.40** | — |
| `claude-sonnet-5-5` | $0.0222 | $25.40 | −7% |
| `claude-haiku-5-5` | $0.0013 | **$4.47** | **−84%** |
| `deepseek-flash` | $0.0031 | $6.63 | −76% |
| `deepseek-v4-pro` | $0.0169 | $19.87 | −27% |

Two things drive these numbers:

1. **Thinking tokens are not free.** Sonnet 5.5 is 33% cheaper per token than
   Sonnet 4.6 yet only 7% cheaper per answer, because it spends ~1 600 output
   tokens where 4.6 spends ~900. DeepSeek V4 Pro spends ~3 600. Cheaper tokens
   do not automatically mean cheaper answers when the model thinks first.
2. **The inline judge floors the cost of cheap models.** Judging costs
   ~$0.0032/query regardless of generator. At Haiku 5.5's $0.0013 generation
   cost that is 70% of the bill, which is why the production saving is 84%
   rather than 95%. Moving the inline judge to `claude-haiku-5-5` ($0.10/$0.50)
   would take the total to roughly $1.5/1 000 — but that changes the gating
   behaviour and needs its own calibration.

## Caveats

- **Judge family bias.** The judge is Anthropic (`claude-haiku-4-5`) scoring both
  Anthropic and DeepSeek answers. DeepSeek V4 Pro scoring *highest* on
  faithfulness suggests the bias is not dominant, but Haiku 5.5's edge on the
  gated dimensions could be partly family affinity. A Gemini judge would remove
  the question; no `GEMINI_API_KEY` was available for this run.
- **Two trials, 30 entries.** Enough to see that gated quality is tied and that
  the citation gap is real; not enough to resolve 0.05 differences.
- **Latency and TTFT were not measured.** Output-token counts above are a rough
  proxy and point the wrong way for the thinking models (Haiku 5.5 writes ~2×
  the tokens of 4.6), so measure end-to-end latency before switching a user-facing
  stream.

## Decision (2026-11-23)

**Keep `claude-sonnet-4-6`.** Gated quality is tied across the field, so a switch
would be a pure cost play, and it costs a measurable citation regression in a
product whose value is grounded, cited answers. The 84% saving from
`claude-haiku-5-5` stays on the table via a citation-focused follow-up
(TASK-9): recover citation correctness with a prompt change and re-measure with
this same protocol, or gate citation so a future downgrade cannot regress it
silently.

No threshold change is needed while the answer model stays put. Verified for the
candidates as well: Haiku 5.5 measures 4.85 faithfulness / 4.90 relevance against
the 4.0 / 4.5 gate.

## Recommendation

1. **Keep `claude-sonnet-4-6` as the answer model for now.** Gated quality is
   tied, and the model that costs the most is also the only one that cites well.
   An −84% cost cut is real, but it buys a measurable citation regression in a
   citation-centric product.
2. **Run a citation-focused follow-up before switching to `claude-haiku-5-5`**
   (the otherwise obvious choice: tied on both gated dimensions, $4.47/1 000):
   either tighten the citation instruction in `SYSTEM_PROMPT` and re-measure, or
   add citation correctness to the gate and accept a lower threshold. If citations
   recover, switch — the saving is 6×.
3. **Reject `claude-sonnet-5-5`** as a cost play: 7% cheaper for a citation
   regression and 1.8× the output tokens.
4. **Keep `deepseek-flash` as the validated alternate** (76% cheaper, gated
   quality tied) and re-test after its next release; `deepseek-v4-pro` is the
   most faithful model measured but 27% cheaper is not enough to justify a
   provider switch on its own.

If a switch is made, `evaluation/gates.py` thresholds (faithfulness ≥ 4.0,
relevance ≥ 4.5) already pass for every model here — Haiku 5.5 measures
4.85/4.90 — so no re-baselining is needed; only `CI_MODEL` and the evaluation
defaults would move to the new answer model.
