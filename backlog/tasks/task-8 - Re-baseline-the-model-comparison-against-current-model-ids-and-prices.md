---
id: TASK-8
title: Re-baseline the model comparison against current model ids and prices
status: In Progress
assignee:
  - '@deepseek'
created_date: '2026-11-23 22:57'
updated_date: '2026-11-24 00:32'
labels:
  - evaluation
  - cost
dependencies: []
references:
  - evaluation/generation.py
  - evaluation/gates.py
  - docs/EVAL.md
priority: high
type: enhancement
ordinal: 27000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The model-switch decision cannot currently be made from this repo. The only stored generation numbers are a single 10-entry run of `claude-sonnet-4-6` (docs/EVAL.md); no DeepSeek comparison has ever been persisted, and the earlier DeepSeek testing used models that have since been retired. Meanwhile `PRICING_PER_MTOK` in evaluation/generation.py is stale: `deepseek-v4-flash` was retired on 2026-09-10, `deepseek-v4-pro` is listed at $0.435/$0.87 but bills at $1.32/$3.96 (peak), DeepSeek peak/off-peak billing is not modelled at all, and current cheaper tiers (`claude-sonnet-5-5` $2/$10, `claude-haiku-5-5` $0.10/$0.50, `deepseek-flash` $0.30/$1.20) are missing. Because `_cost_usd` returns None for an unknown id, a renamed model silently reports no cost instead of failing. The harness already supports what is needed (`--compare`, `--trials` mean/std, measured token cost), so this is about correct inputs, a decision-grade run, and a persisted record.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 PRICING_PER_MTOK carries current published rates for the models in scope, with source URLs and a sync date recorded in the module, and DeepSeek peak rates documented as an upper bound (off-peak bills at half)
- [x] #2 Every model id used in the comparison is exercised against its live API, so a retired or renamed id fails loudly instead of silently routing or reporting no cost
- [x] #3 A comparison run covers all 30 golden entries across the current production model and the candidate models, with at least 2 trials, reporting per-model mean and standard deviation for faithfulness, relevance and citation plus measured cost per query
- [x] #4 The comparison is judged by a model outside the compared families where possible, or the judge-family bias is stated explicitly in the recorded results
- [x] #5 Results are committed as JSON plus a markdown summary naming the date, judge model, sample size and price snapshot, so a future decision can be audited without re-running
- [ ] #6 The outcome is recorded as a keep-or-switch decision with the quality delta and the cost delta per 1000 queries
- [ ] #7 If a switch is chosen, the CI gate thresholds in evaluation/gates.py are re-baselined against the chosen model, since the current 4.0/4.5 values were calibrated on claude-sonnet-4-6 output
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Refresh PRICING_PER_MTOK from the published price sheets (DeepSeek peak schedule as the upper bound; Anthropic flat) and drop the retired deepseek-v4-flash id so an unknown model reports no cost instead of a wrong one. Record sources and a sync date next to the table.
2. Exercise every id in scope with one live call each (claude-sonnet-4-6, claude-sonnet-5-5, claude-haiku-5-5, deepseek-flash, deepseek-v4-pro) before spending on the full run, checking token usage and that _cost_usd returns a number.
3. Run the comparison across all 30 golden entries with 2 trials (mean/std per dimension), current production sonnet-4-6 as the baseline plus the candidates, judged by claude-haiku-4-5 (no Gemini key available; the family bias is stated in the results).
4. Commit results as JSON plus a markdown summary naming date, judge, sample size and price snapshot; record the keep-or-switch decision with quality and cost deltas.
5. If switching, re-baseline the CI gate thresholds against the chosen model.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Comparison run 2026-11-23 (30 entries x 2 trials x 5 models, judge claude-haiku-4-5, ~52 min, ~$5.7 of API spend; raw scores in docs/model_comparison_2026-11-23.json, write-up in docs/MODEL_COMPARISON.md).

Results (mean +- std across the 2 trials, n=30):
- claude-sonnet-4-6: faithfulness 4.80 +-0.00, relevance 4.87 +-0.00, citation 4.65 +-0.03, generation $0.0241/q, 906 output tokens
- claude-sonnet-5-5: 4.79 +-0.02, 4.92 +-0.07, 4.37 +-0.09, $0.0222/q, 1598 tokens
- claude-haiku-5-5: 4.85 +-0.07, 4.90 +-0.10, 4.48 +-0.07, $0.0013/q, 1884 tokens
- deepseek-flash: 4.87 +-0.09, 4.82 +-0.07, 4.37 +-0.19, $0.0031/q, 2118 tokens
- deepseek-v4-pro: 4.99 +-0.02, 4.85 +-0.03, 4.51 +-0.16, $0.0169/q, 3643 tokens

Findings:
1. Both gated dimensions are tied inside the noise band (largest faithfulness spread 0.20 vs std up to 0.09; relevance spread 0.10 vs std up to 0.10). No model earns a quality-based preference.
2. Citation correctness is the only consistent difference: sonnet-4-6 4.65 vs 4.37-4.51 for everything else, in both trials, with a gap larger than the trial std. It is ungated by design (every model produced a 2 on the same sample), but it is the product's core value.
3. Production-equivalent cost (generation + the single inline faithfulness judge that rag.py runs per answer) per 1000 queries: sonnet-4-6 $27.40, sonnet-5-5 $25.40, haiku-5-5 $4.47, deepseek-flash $6.63, v4-pro $19.87. The 3-judge eval totals are higher and overstate production judging; the doc explains both.
4. Thinking tokens defeat naive per-token pricing: sonnet-5-5 is 33% cheaper per token than 4-6 but only 7% cheaper per answer, and v4-pro writes ~4x the output tokens.
5. The inline judge floors cheap models: ~$0.0032/query of judging is 70% of haiku-5-5's bill, so the real saving is 84% not 95%. Moving the inline judge to claude-haiku-5-5 would get to ~$1.5/1000 but changes gating behaviour and needs its own calibration.

Recommendation recorded: keep sonnet-4-6 for now; before switching to haiku-5-5 (tied quality, 6x cheaper), run a citation-focused follow-up (tighten the citation instruction in SYSTEM_PROMPT and re-measure, or gate citation and accept a lower threshold); reject sonnet-5-5 as a cost play; keep deepseek-flash as the validated alternate. Gate thresholds need no re-baselining for any candidate (haiku-5-5 measures 4.85/4.90 against 4.0/4.5) - only CI_MODEL and the eval defaults would move.

Blocker found and fixed before the run: claude-sonnet-5-5 and claude-haiku-5-5 return a thinking block first, so response.content[0].text raised AttributeError and those models could not be evaluated (or served in production). Added llm_response.text_of() at all six call sites plus max-token overrides so thinking does not truncate answers. Recorded in commit 5df8b7d.

Waiting on: the user's keep-or-switch decision (AC 6/7), then either a citation follow-up task or the switch itself.
<!-- SECTION:NOTES:END -->
