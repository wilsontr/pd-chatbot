# Article notes: pd-chatbot model/cost eval

Raw material for a writeup. Two parts: (1) final findings, (2) the story arc that produced them.

---

## Part 1: Final findings

### The headline number

**DeepSeek v4-flash costs ~56x less than Claude Sonnet 4.6 per query, for a faithfulness
score that's statistically indistinguishable on an easy eval and only modestly behind on
a hard one.** Cost separates models by orders of magnitude; quality barely separates them
at all — and the size of that quality gap depends heavily on how hard the eval questions are
and which model judges them.

### Easy-set comparison (n=30, "how do I use this one object" questions)

Judge: gemini-3.5-flash, 3 trials, mean ± std.

| Model | Faithfulness | Relevance | Citation correctness | Cost / 1,000 queries |
|---|---|---|---|---|
| Sonnet 4.6 | 4.92 ± 0.02 | 5.00 ± 0.00 | 5.00 ± 0.00 | $24.62 |
| Haiku 4.5 | 4.89 ± 0.11 | 4.99 ± 0.02 | 4.98 ± 0.02 | $6.46 |
| DeepSeek v4-pro | 4.89 ± 0.07 | 5.00 ± 0.00 | 4.90 ± 0.06 | $1.86 |
| DeepSeek v4-flash | 4.78 ± 0.10 | 4.99 ± 0.02 | 4.94 ± 0.02 | $0.44 |

All four models are within ~0.14 points of each other on every dimension — well inside
trial-to-trial noise. On an eval this easy, model choice barely matters for quality and
cost is the only axis that meaningfully differs.

### Hard-set comparison (n=12, multi-step synthesis / adversarial-knowledge questions)

Same judge, 2 trials, mean across trials (see Part 2 for how/why this set was built).

| Model | Faithfulness (trial 1 / trial 2 / avg) | Relevance avg | Citation avg |
|---|---|---|---|
| Sonnet 4.6 | 5.00 / 4.83 / **4.92** | 4.96 | 5.00 |
| DeepSeek v4-pro | 4.83 / 4.83 / **4.83** | 4.96 | 4.92 |
| DeepSeek v4-flash | 4.50 / 4.73 / **4.62** | 5.00 | 4.79 |
| Haiku 4.5 | 4.75 / 4.50 / **4.63** | 4.80 | 4.96 |

Once questions actually demand multi-object synthesis, tradeoff reasoning, and honesty
about undocumented behavior, real separation appears — Sonnet leads, but not by a landslide,
and not without its own miss.

### Concrete, namable failures (the actual substance for a post)

- **Sonnet is not immune.** Trial 2, question h_007 (build a Pd-native stereo reverb):
  Sonnet stated a specific feedback-gain value ("1.0") and attributed it to the docs — the
  docs don't specify this. One clean fabrication in 24 answers (12 questions × 2 trials).
- **The same conceptual bug hit two unrelated models independently.** Question h_002
  (MIDI note-off handling): both Haiku and DeepSeek v4-flash used `[stripnote]` to *detect*
  note-off messages — but stripnote's entire documented job is to *filter out* note-offs.
  Two different vendors, same wrong mental model. Worth more than either mistake alone.
- **DeepSeek fabricates specifics more than prose.** Recurring pattern across both the easy
  and hard sets:
  - Invented a nonexistent second inlet on `tabwrite~` (h_003, both v4-flash and v4-pro).
  - Invented entirely fictional object names — `ldelwrite~`, `rdelread~` — in a synthesized
    patch (h_007, v4-flash).
  - Earlier (easy set, first Haiku-judged pass): fabricated documentation URLs even when
    surrounding content was accurate — later confirmed as a *general* cheap-model pattern
    (Haiku showed a milder version of the same thing), not DeepSeek-specific.
- **Faithfulness and relevance aren't the same axis, and this eval design assumed they
  mostly were.** Question h_011 ("what happens with a negative frequency into osc~?" —
  deliberately undocumented): Haiku correctly said the docs don't specify — faithfulness 5 —
  but relevance dropped to 2 because it never reasoned through the answer (cosine's
  even-symmetry is inferable without doc support). An answer can be perfectly honest and
  still useless. That's a real, generalizable methodology point, not a pd-chatbot-specific one.
- **Judge choice materially changes the conclusion.** Under Haiku-as-judge (the original,
  same-family setup), DeepSeek's citation-correctness looked dramatically worse than
  Sonnet's (4.07–4.43 vs 4.83). Under Gemini-as-judge (outside both compared vendor
  families), that gap nearly disappeared (4.90–4.98 vs 5.00). Whichever mechanism drove
  that — same-family favoritism, or just an idiosyncrasy of that specific judge — swapping
  judges changed the headline comparison. That's arguably the most important methodology
  finding of the whole project: an eval's conclusion can be an artifact of which model grades it.

### What would actually move these numbers further (if asked "so what next")

- Faithfulness/relevance are near ceiling on the easy set — not worth optimizing further;
  the eval itself is the bottleneck, not the models. That's what motivated the hard set.
- Citation correctness has a cheap, structural fix that doesn't depend on model choice at
  all: stop trusting the model to transcribe URLs from memory. Post-process the answer to
  strip any model-emitted citation and append a "Sources" list built directly from the
  retrieved chunks' actual URLs. This would push every model to ~5.0 on that dimension,
  since it removes the failure mode rather than hoping the model avoids it.

---

## Part 2: The story arc (chronological, for narrative structure)

This is roughly the order things actually happened, useful for pacing a "here's how I found
this" narrative rather than presenting the final table cold.

1. **Starting point.** pd-chatbot already had solid RAG engineering (hybrid retrieval,
   prompt caching, streaming) but *no way to know if any of it worked*. A prioritization doc
   (PROD_WORTHY.md) called this out explicitly: the missing piece wasn't more engineering,
   it was an evaluation harness — the "defining habit of a production LLM engineer is
   refusing to ship on intuition."

2. **Built the eval harness.** LLM-as-judge scoring on faithfulness/relevance/citation
   correctness, golden dataset, CI integration already existed from prior work. Then:
   real users asked "how do I stop this thing from hallucinating even with retrieval in
   place?" — leading to a faithfulness-gating feature in the live app itself (not just eval):
   inline judge-scored retry on low-faithfulness answers, tightened system prompt
   (explicit "flag when you go beyond the docs" instruction).

3. **First sign the eval itself was untrustworthy.** Running `--compare` against Haiku
   produced results "closer than expected" — Haiku edging out Sonnet on faithfulness. That
   surprise, rather than being reported as a finding, became the actual investigation:
   - Judge responses were silently truncating (`max_tokens=200` too low, long
     hallucination-heavy explanations got cut off mid-JSON, and unparseable output silently
     dropped the score entirely rather than counting it as a failure). Fixed: raised token
     budget, added a regex fallback to recover a score from truncated JSON instead of
     discarding it.
   - Separately: the judge only saw a 500-char-truncated slice of each context chunk, while
     the model being graded saw the *full* chunk (median chunk length: 1,025 chars; 80%
     exceeded 500). The judge was penalizing claims it simply couldn't see the support for.
   - The rubric itself conflated "synthesized example" (a deliberate product feature —
     the assistant is supposed to build patch diagrams) with "fabrication." Rewrote the
     rubric to only penalize claims that contradict context or invent unstated specifics,
     not synthesis grounded in real documented behavior.
   - **Result of fixing all three: the original conclusion reversed.** Sonnet went from
     "slightly behind Haiku" to a clean lead, and the corrected data showed *why* — Sonnet's
     floor (min score across the set) was 4/5 with zero real fabrications; Haiku's had one
     genuine one. Same mean, different reliability at the tail.

4. **Brought in DeepSeek for a cost comparison.** v4-flash and v4-pro wired in via an
   OpenAI-compatible client. Immediately hit two more transparent, fixable bugs rather than
   just reporting numbers:
   - `deepseek-v4-pro` is a reasoning model; hidden `reasoning_content` tokens count against
     the same `max_tokens` budget as the visible answer. Two questions came back completely
     empty because the model spent its entire budget "thinking" before writing anything
     visible. Fixed by giving that specific model a much larger budget.
   - DeepSeek's citation-correctness score looked dramatically worse than Sonnet's/Haiku's —
     but the failure mode was specifically *fabricated URLs*, not wrong facts. Worth
     separating "the model is wrong" from "the model cites confidently and incorrectly."

5. **Realized the judge itself was compromised.** The default judge model was Haiku — which
   was also one of the models being scored. Self-family judging is an obvious methodology
   hole (the kind an adversarial reader finds in five minutes). Rather than just switching
   quietly, the plan became: swap to a fully neutral judge (outside both Anthropic and
   DeepSeek) and see whether the conclusion moved. It did — significantly (see "judge choice"
   finding above).

6. **Getting a neutral judge working was its own saga**, worth a short "engineering diary"
   aside if the post wants texture:
   - `gemini-3.1-pro` (first choice) blocked entirely on a zero-quota free tier, then even
     after enabling billing, hit a **250-requests/day cap** — too low to complete even a
     single trial (which needs ~360 judge calls). Switched to `gemini-3.5-flash`.
   - `gemini-3.5-flash` turned out to be *also* a thinking model with the same hidden-token
     budget problem as DeepSeek v4-pro — first attempts silently truncated. Fixed the same
     way: bigger output budget.
   - Long eval runs kept dying — sometimes from real system memory pressure (traced via
     `vm_stat`, correlated with a browser using most of available RAM), sometimes from a
     genuinely transient `anthropic.OverloadedError: 529` with no retry logic anywhere in
     the eval harness or even the core app's own classification step. Fixed by adding
     bounded-exponential-backoff retries around every provider call, in both the eval
     harness and the production `rag.py` request path — a real production hardening
     improvement that came out of chasing eval flakiness, not the other way around.

7. **Ran the full 4-model, 3-trial comparison with the fixed judge.** This produced the easy-set
   table above: near-ceiling, near-identical quality, order-of-magnitude cost gap.

8. **"These are all pretty good — what would actually move the needle?"** Realized the
   remaining headroom split into two different problems: citation correctness has a cheap
   structural fix (stop trusting model-transcribed URLs); faithfulness is stuck near ceiling
   because *the eval questions are too easy to discriminate*, not because the models
   are equally good at everything.

9. **Built a harder, adversarial-by-design question set** (12 questions) specifically to
   stress faithfulness: multi-object patch synthesis, comparison questions that force holding
   two documented behaviors in tension, deliberately-thin-documentation questions (does the
   model say "I don't know" or fabricate confidently?), and — the most aggressive category —
   questions that require real audio-DSP knowledge the corpus doesn't teach at all (bucket-brigade
   delay effects, Pd-native-only reverb, granular synthesis, Buchla-style wavefolding), to see
   whether models correctly flag "this isn't in the docs" while still reasoning correctly, versus
   inventing a nonexistent object to sound authoritative.

10. **The harder set worked exactly as intended** — see Part 1's "concrete failures" for what
    it surfaced. Real separation, real cross-model shared mistakes, real evidence that
    "faithful" and "helpful" can pull apart.

### Suggested narrative shape

Two honest options, pick one:

- **"I built an eval, trusted a surprising result, and that was the mistake"** — lead with
  the Haiku-beats-Sonnet reversal (step 3), use it to earn the reader's trust that the final
  numbers are real, then land on the cost/quality gap and the harder-set findings as the payoff.
- **"Cost, quality, and the hidden cost of measuring wrong"** — lead with the final table
  (cost gap is huge, quality gap is tiny-until-you-look-harder), then use the debugging
  journey as the evidence for *why* the reader should trust that claim instead of the
  dozen other "we benchmarked model X vs Y" posts that don't show their work.

Either way, the strongest single sentence available: *the size of the quality gap between
a frontier model and a fraction of its cost depends entirely on how hard you're willing to
look for it — and on making sure the thing doing the looking isn't grading its own family's homework.*
