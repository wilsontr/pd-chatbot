# Failure modes the evals caught

An agent is only as interesting as the ways it goes wrong and how you find out.
This is a record of the failure modes observed while building pd-chatbot — each
with the measurement that surfaced it, the fix, and the check that now stops it
coming back.

The pattern worth noticing: **almost none of these were found by reading an
answer.** They were found by running the same entries on two arms, by running
the final patches in a sandbox and machine-checking a spec, or by a
deterministic check that fails a build. Several were found by discovering that
the *judge itself* was wrong.

| # | Failure | Symptom | Found by | Now caught by |
|---|---|---|---|---|
| 1 | `[line~]` list order | "a list `100 500`" implies right-to-left inlet fill — wrong in Pd | pipeline-vs-agent eval, q_004 | deterministic check + attribution prompt rule |
| 2 | `[unpack]` outlet order | asserted an ordering the retrieved excerpt does not state | judge explanation + claim warning, q_007 | `outlet-order-unsupported` warning (2/3 → 0/5) |
| 3 | pd-patch inlet counts | `"inlets": 2` on `[snapshot~]` while the prose admitted it was unconfirmed | manual audit of 5 runs; the judge caught 1 in 5 | canonicalised at the patch boundary (`agent/patch_counts.py`) |
| 4 | The judge's own Pd belief | scored a reversed example 5/5/5 and a correct answer 2 | recorded control answers | rubric rule + control answers pinned both directions |
| 5 | Patches that run but don't work | 88% ran cleanly, only 73% met the spec | sandbox + machine-checked spec, not the judge | `evaluation/patch_eval.py` task specs |

## 1. A true convention applied to the wrong object

**Symptom.** Asked "how does `[line~]` work and what's the gotcha with its right
inlet?", the agent answered: *"send the time and the target together, for example
a list `100 500`"*. A list arriving at the leftmost inlet is distributed **left to
right**, so that example sets the target to 100 and the ramp time to 500 — the
opposite of what the answer says. The judge scored faithfulness 2.

**Why the agent did it.** The `[line~]` excerpt says only that "a list of two
floats distributes the value over the inlets, as usual in Pd". "As usual" is true
of **outlets** (right to left) and the corpus states that rule for outlets; the
model applied the salient rule it knew to the case at hand.

**Fix.** Tool guidance plus an attribution rule in the agent prompt: a general
chapter that merely mentions an object is not evidence for how that object
behaves, and a claim the excerpts do not state must not be asserted
(TASK-5.20).

**Now caught by.** `evaluation/claims.py::check_line_list_order` runs on every
scored answer in both arms and fails the gate on a right-to-left list example —
deterministically, so repeating the run does not make it go away.

## 2. Unsupported specifics: the outlet-order claim

**Symptom.** On q_007 ("how do `[pack]` and `[unpack]` work?"), the agent scored
faithfulness **2 / 4 / 3 (mean 3.00)** against the pipeline's 4.67 on the same
entry. The judge's explanation was "outlet firing order … beyond documentation".

**Why the agent did it.** The claim is true Pd — an object with several outlets
sends them right to left — but the corpus states it in exactly one place
(§2.4.4), and the `[unpack]` reference page retrieval returned does not. The
attribution rule above existed but was too abstract to beat the model's prior.

**Fix.** The rule was made concrete for object-level conventions
(TASK-5.23). The measured effect on this exact failure: the
`outlet-order-unsupported` warning fired in **2 of 3** trials before the change
and **0 of 5** after.

**Now caught by.** Same check family: a warning today, not an error, because the
model makes the claim often enough that gating it would make the build flaky.
The severity decision is recorded on the task.

## 3. The diagram contradicts the prose in the same answer

**Symptom.** On q_006 the agent wrote a `[snapshot~]` object with `"inlets": 2`
into the pd-patch JSON — in **4 of 5** audited answers — while the prose in the
*same answer* said the second inlet was not documented. The IEM reference lists a
single inlet row.

**Why the judge missed it.** The judge scored only one of those runs 2. The eval
therefore *under-reported* the error rate: it was catching this failure less than
a quarter of the time. That is the important part — the measurement looked fine.

**Fix.** Not a prompt rule. Prompt text measurably stopped the outlet-order claim
but did not stop the diagram carrying a count the answer admits it cannot
confirm, so the repair moved to the boundary: inlet/outlet counts are
canonicalised against the retrieved reference before validation
(`agent/patch_counts.py`). A count contradicting a fixed reference count is
rewritten; variable-arity objects (`[pack]`, `[dac~]`) and objects the excerpts
do not cover are left alone, as is any object whose connections would fall
outside the repaired count.

**Now caught by.** After the change, **0 of 180** answers in the 30-entry run
carry a `[snapshot~]` count other than 1, and q_006 scores 5/5 on five runs. The
eval applies the same canonicalisation to both arms so the comparison stays fair,
and repairs are counted under `Custom/PdPatch/CountRepaired`.

## 4. The judge was wrong, in both directions

The most useful failure to catch. The faithfulness judge held the wrong belief
about Pd inlet order, so faithful answers were penalised and a self-contradicting
one was rewarded:

| Answer | Judge score |
|---|---|
| "a list such as `1000 0.5` (time, then target)" — contradicts itself | **5 / 5 / 5** |
| "`500 1000` would set the target to 500 in the left inlet and the time to 1000 ms in the right…" — correct | **2** |

It affects both arms identically, so the published comparison stays fair — but
the variance it adds is measurement noise, not answer quality, and per-entry
scores on this rule were unusable.

**Fix.** The rubric now states the documented rule (inlets fill left to right
from a list at the leftmost inlet; right-to-left is about outlets) without naming
any golden entry's answer. Control answers pin both directions in
`evaluation/judge_controls.py`: the reversed control scores 1–2 and the correct
one 5, on every run.

**Lesson.** A judge is a measuring instrument and needs its own tests. The
generation gate's thresholds were calibrated against the old rubric, so a rubric
change invalidates them: re-measuring, and an explicit decision on whether the
thresholds still hold, is part of the fix rather than an afterthought.

## 5. Running the patch found what the judge could not

The judge scores prose. It cannot tell whether a patch makes sound.

**Symptom.** With no sandbox, 88% of final patches ran cleanly but only **73%**
met the task spec. Some were silent; some made the wrong noise.

**Fix.** Give the agent `run_patch` and `analyze_audio`, and let it iterate: run,
read the Pd console, measure level and pitch, rewrite.

**Measured** (16 tasks × 3 trials per arm, same model, no judge — final patches
run in the sandbox and machine-checked against a spec):

| | Without execution | With execution |
|---|---|---|
| Runs cleanly | 88% | 98% |
| Runs cleanly **and** meets the spec | **73%** | **92%** |
| Cost per answer | $0.0024 | $0.0041 |
| Tool calls | 3.3 | 6.3 |
| Patch runs | 0.0 | 1.5 |

Every spec is pinned by a correct patch and a near miss
(`tests/test_patch_eval.py`), so the metric cannot drift into measuring the wrong
thing. Details in [PATCH_EVAL.md](PATCH_EVAL.md).

## 6. Honest failure, on the record

Not every failure mode is a bug. Recording the demo run
([`assets/self-correction.gif`](../assets/self-correction.gif)) asked for a
3-second reverb. The first two attempts reached for Pd library reverberators,
which the sandbox's vanilla Pd does not have:

```
<- run_patch [ok (1770 ms)]: ran sine-reverb-tail: 1 error(s)
      error: ... couldn't create
<- analyze_audio [ok (0 ms)]: analyzed sine-reverb-tail: silent
<- run_patch [ok (1607 ms)]: ran sine-reverb-tail: 1 error(s)
      error: ... couldn't create
```

It searched again, replaced them with a feedback delay line
(`[delwrite~ tail 3000]` / `[delread~ tail 1000]` / `[*~ 0.85]`), re-ran clean
and rendered audible audio at 220 Hz. Then it said plainly what the result is
not:

> **Important: this is not a true reverb.** The `[rev1~]` and `[rev2~]` objects
> both failed to create in this environment, so I couldn't use them. […] Its
> repeats fade out with each pass, giving a tail that is a series of echoes
> rather than a smooth reverb.

It also flagged that the feedback gain "needs lowering before this is used for
real". Reporting the unmet constraint instead of claiming success is the
behaviour the prompt asks for, and it is worth measuring as much as the
successes.

## What still gets through

- **Judge noise.** Unchanged prompts have moved individual entries by up to two
  points between runs, and the citation dimension swings 2 ↔ 5 for near-identical
  answers. That is why single-trial per-entry scores are never quoted, and why the
  published comparison is 30 entries × 3 trials with mean ± std.
- **Deterministic checks are narrow.** They cover claims that have already been
  observed. A true claim the excerpts happen not to state is published as a
  warning rather than an error, because gating it would make the build flaky.
- **libpd is not desktop Pd.** The sandbox reports `verbose(n):` prefixes and
  ignores `; pd exit`, so console-derived signals differ slightly from a GUI
  session.
- **The scope gate is a guardrail, not a security boundary.** It fails open to
  in-scope on unknown labels, and there is no output moderation.

## How this is gated today

| Gate | Threshold |
|---|---|
| Retrieval (`pytest tests/`) | recall@5 ≥ 60%, MRR ≥ 0.30, hybrid ≥ both ablations |
| Generation (sampled, per PR) | faithfulness ≥ 4.0, relevance ≥ 4.5, citation ≥ 4.0 |
| Scope adherence | ≥ 90% (currently 100% of 20 cases) |
| Deterministic claim checks | 0 errors across both arms — any violation fails |
| Agent gate | task success ≥ 60%, ≤ 3.5 tool calls, ≤ $0.0300/task, p95 ≤ 20 s, recovery ≥ 50% |

Thresholds and their rationale live in
[`evaluation/gates.py`](../evaluation/gates.py).

## Reproduce it

```bash
# the 30-entry, 3-trial both-arm comparison behind docs/EVAL.md
python -m evaluation.evaluate --all --mode both --agent-retrieval --sample 30 \
  --trials 3 --report docs/EVAL.md

# patch specs: with and without sandbox execution
python -m evaluation.patch_eval --trials 3 --report report.md --output results.json

# the claim checks on their own
pytest tests/test_claims.py -q
```
