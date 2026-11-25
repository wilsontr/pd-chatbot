# Patch evaluation

Share of answers whose patch runs without a Pd error **and** meets a
machine-checked spec, with and without the tools that let the agent run its
patches (TASK-5.5). 16 tasks x 3 trials per arm, `claude-haiku-5-5`, 2026-11-24.
No LLM judges the result: final patches from both arms are run in the sandbox and
measured. Every spec is pinned by a correct patch and a near miss in
[`tests/test_patch_eval.py`](../tests/test_patch_eval.py).

```bash
python -m evaluation.patch_eval --trials 3 --report report.md --output results.json
```

| | Without execution | With execution |
|---|---|---|
| Answers | 48 | 48 |
| Wrote a patch | 100% | 100% |
| Runs cleanly | 88% | 98% |
| **Runs cleanly and meets the spec** | **73%** | **92%** |
| By trial | [0.688, 0.812, 0.688] | [0.812, 0.938, 1] |
| Cost per answer | $0.0024 | $0.0041 |
| Latency p50 / s | 8.4 | 11.2 |
| Latency p95 / s | 10.4 | 25.8 |
| Tool calls | 3.3 | 6.3 |
| Patch runs | 0.0 | 1.5 |
| Infrastructure errors (excluded) | 0 | 0 |

| Task | Without execution | With execution |
|---|---|---|
| p_01 | 3/3 | 3/3 |
| p_02 | 0/3 | 2/3 |
| p_03 | 2/3 | 3/3 |
| p_04 | 3/3 | 3/3 |
| p_05 | 3/3 | 3/3 |
| p_06 | 1/3 | 3/3 |
| p_07 | 3/3 | 3/3 |
| p_08 | 3/3 | 2/3 |
| p_09 | 2/3 | 3/3 |
| p_10 | 3/3 | 3/3 |
| p_11 | 2/3 | 2/3 |
| p_12 | 3/3 | 3/3 |
| p_13 | 3/3 | 3/3 |
| p_14 | 0/3 | 2/3 |
| p_15 | 1/3 | 3/3 |
| p_16 | 3/3 | 3/3 |

Two earlier runs of the same eval, while the prompt and loop were being changed,
scored 79% without and 85% with, then 77% and 90%. The prompt changes were general
(keep revising while a check fails) and add no Pd facts, but they were made after
seeing failures on these tasks, so treat the gap as an estimate, not a held-out result.

Documentation questions, first 15 golden entries x 3 trials, agent arm (judge
`claude-haiku-4-5`):

| | Faithfulness | Relevance | Citation |
|---|---|---|---|
| Before TASK-5.5 | 4.73 ± 0.27 | 5.00 ± 0.00 | 4.62 ± 0.08 |
| `write_patch` only | 4.58 ± 0.34 | 4.93 ± 0.12 | 4.62 ± 0.31 |
| With execution | 4.62 ± 0.30 | 4.89 ± 0.14 | 4.69 ± 0.16 |
