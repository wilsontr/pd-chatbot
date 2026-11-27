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

## Wavetables (TASK-5.28)

p_14 asks for a table filled with one sine cycle on load and played at 220 Hz.
Its failures all ran cleanly and were **silent**. In every failure the table was
never filled; the read side was fine. The causes were a fill loop with no wire
from `[until]` to `[f]`, a `0` sent to `[until]` (zero iterations), and a
`[sinesum …(` message box sent nowhere because it had no leading `;` and no wire.
The fix is the `wavetables` skill (documented `sinesum` + `[tabosc4~]` route
first) plus a general execution rule: when a run is clean but silent, read the
suspect values back with `[print]` before rewiring. The failing shapes and every
prescribed route are pinned in the sandbox by
[`tests/test_wavetables.py`](../tests/test_wavetables.py).

| p_14, with execution, 6 trials | Pass | Cost per answer | Latency p50 / s |
|---|---|---|---|
| Before | 1/6 (5 silent) | $0.0124 | 31.1 |
| Execution rule only | 3/6 | $0.0107 | 24.2 |
| Rule + `wavetables` skill | **6/6** | $0.0046 | 10.1 |

Full eval re-run after the change (16 x 3 per arm, 2026-11-27): **75%** without
execution (was 73%) and **92%** with execution (unchanged). p_14 is now 3/3 in
both arms (was 0/3 and 2/3). The other tasks move by a trial either way, which
is within the run-to-run spread noted above.
