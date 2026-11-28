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

## In CI (TASK-5.36)

[`patch-eval.yml`](../.github/workflows/patch-eval.yml) runs the with-execution
arm on every pull request that touches the agent, the sandbox, the skills or this
eval. It builds the sandbox image, runs the 16 tasks once, and fails when the
gate in [`evaluation/gates.py`](../evaluation/gates.py) is breached. The agent's
bounds depend on whether it can run patches, so the gate keeps one set per mode:

| Bound | Docs mode (`evaluate --gate-agent`) | Execution mode (`patch_eval --gate`) |
|---|---|---|
| Task success | judged >= 60% | patch passes >= 75% (`PATCH_EVAL_PASS_FLOOR`) |
| Tool calls per answer | <= 3.5 | <= 10 |
| Cost per answer | <= $0.03 | <= $0.03 |
| p95 latency | <= 20 s | <= 60 s |
| Recovery rate (2+ error tasks) | >= 50% | >= 50% |

The execution bounds come from the table below: 92% pass, 81% in the worst trial
(so 12/16 for one trial), 6.3 tool calls and a 25.8 s p95. One run of the CI
command (16 x 1, 2026-11-28) passed 16/16 with 7.1 tool calls, a 43.9 s p95 and
$0.0847 in all; with one trial the p95 is the slowest answer, hence 60 s. The docs bounds would
fail every healthy execution run. A run where more than a quarter of the answers
hit infrastructure errors fails as unmeasured rather than passing on what is left.

Cost: 16 answers at about $0.0041, so roughly **$0.07-0.09 a run** ($0.0847 measured), with no judge
calls. The step summary states the measured dollars. To run more trials, use
the workflow's manual dispatch.

```bash
python -m evaluation.patch_eval --arm with --gate --summary summary.md
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
