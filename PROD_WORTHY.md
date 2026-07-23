# Making pd-chatbot Production-LLM-Worthy

A prioritized plan to evolve pd-chatbot from a well-engineered RAG app into a
project that demonstrably shows production LLM development competency — for
resume/portfolio purposes.

## Where pd-chatbot stands today

This is not a toy. Already in place:

- Parent-child chunking
- Hybrid retrieval (vector + BM25) with Reciprocal Rank Fusion
- An LLM query-classifier/rewriter (Haiku)
- Prompt caching via `cache_control`
- SSE streaming responses
- History compression via Haiku
- Proxy-aware rate limiting
- Real APM (New Relic)

That is strong **software engineering**.

**The gap:** there is no way to know if any of it works, and no way to prove a
change made it better. The retrieval strategy, the rewrite step, the model
choice — all rest on intuition. The defining habit of a production LLM engineer
is refusing to ship on intuition. Closing that gap is the story the resume is
missing.

Priority order below. **Item 1 is worth more than all the others combined.**

---

## 1. Build an evaluation harness (the headline gap)

*The* production-LLM competency, currently absent entirely.

- **Golden dataset**: 40–80 hand-curated Pd questions with known-relevant source
  sections and reference answers. Mix object-reference, conceptual, and
  multi-turn.
- **Retrieval metrics**: recall@k, MRR, nDCG. Proves hybrid+RRF beats
  vector-only instead of asserting it in the README.
- **Generation metrics**: faithfulness/groundedness (does the answer stick to
  retrieved context?), answer relevance, citation correctness. Use
  **LLM-as-judge** (Claude grading Claude) — building a good judge rubric is
  itself a resume-grade skill.
- **Run it in CI** (GitHub Actions — there is currently no `.github/` at all) so
  a prompt or chunking change that regresses quality fails the build.

**Concepts to learn:** RAG evaluation (RAGAS framework), LLM-as-judge and its
failure modes (position bias, verbosity bias), retrieval IR metrics, the
offline-eval vs. online-eval distinction.

**Tools to touch:** RAGAS, promptfoo, or DeepEval.

**Resume payoff:** rewrites the bullet from *"built a RAG chatbot"* to *"built
an evaluated RAG system with a 60-question golden set, LLM-as-judge faithfulness
scoring, and retrieval-regression gating in CI."*

---

## 2. LLM-native observability + cost tracking

New Relic gives latency and errors, but nothing LLM-specific.

- **Tokens and $ per request** (input/output/cache-read split — prompt caching is
  already in use, so quantify the savings).
- **Trace trees** over the classify → embed → retrieve → generate pipeline with
  the retrieved chunks attached, so a bad answer can be debugged by seeing what
  was retrieved.

**Concepts:** LLM tracing and spans, token accounting, cost attribution.

**Tools:** Langfuse or Arize Phoenix (both open-source, self-hostable), alongside
New Relic.

---

## 3. Reliable structured outputs for `pd-patch`

The patch JSON is currently coaxed out via prompt instructions and parsed
loosely — a classic production incident source.

- Move it to Anthropic **tool-use / structured output** with a real schema.
- Validate with Pydantic.
- **Retry-on-invalid.**
- Track patch-JSON parse-failure rate as a metric (ties into #2).

**Concepts:** constrained/structured generation, tool-use for extraction, schema
validation + repair loops.

---

## 4. A feedback loop

Add thumbs up/down in the frontend; persist
`(query, retrieved chunks, answer, rating)`.

- Grows an eval set from real usage.
- Supplies the "we close the loop on quality" narrative.

**Concepts:** online eval, data flywheel, using production traffic to grow test
sets.

---

## 5. Guardrails (lighter, but worth naming)

Basic prompt-injection awareness and groundedness gating. The system prompt
already says "prefer the documentation" — formalize it into a check.

**Concepts:** prompt injection, jailbreak/refusal handling, groundedness
thresholds.

---

## Skip / deprioritize

Most of the current `IMPROVEMENTS.md` (Redis cache, managed vector DB, ChromaDB
persistence). Good instincts, but they signal "backend engineer" not "LLM
engineer," and at current traffic they are premature. Do them *after* the list
above, if ever.

---

## Recommendation

Do **#1 next**. It is the highest resume-leverage work in the repo, and it
retroactively makes everything already built *demonstrably* good instead of
plausibly good.
