# Pd Documentation Assistant

An evaluated **tool-use agent** for [Pure Data](http://msp.ucsd.edu/Pd_documentation/)
(Pd), the visual programming language for audio. It searches the official Pd
documentation, writes real patches, runs them in a headless Pd sandbox, listens
to the rendered audio, and fixes what it hears before it answers.

![The self-correction loop: a patch breaks, Pd reports the error, the agent rewrites it, and the rendered audio plays](assets/self-correction.gif)

*Recorded from a real run — `pdbot ask` with `PD_SANDBOX_BACKEND=docker`, not a
hand-written transcript: the agent wrote a reverb patch, `[rev1~]` failed to
create in vanilla Pd, it rebuilt the effect from a feedback delay line, and the
audio at the bottom is the WAV that run produced.*

Measured against the tuned RAG pipeline it replaced, on the same 30-question
golden set, three trials per arm:

| | Result |
|---|---|
| Answer quality | level with the pipeline — faithfulness **4.61** vs 4.63, relevance 4.86 vs 4.81, citation 4.48 vs 4.50 |
| Retrieval | its own `search_docs` calls beat the pipeline's fixed search — recall@5 **76.1%** vs 75.0%, MRR 0.703 vs 0.696 |
| Patches | running its own patches lifts spec-compliant output from **73% to 92%** |

The loop does not buy answer quality; it buys the mechanism — re-searching,
running tools, correcting itself. Where that pays off is measured, and where it
still fails is written up in the [failure modes](docs/FAILURE_MODES.md).

Numbers: [EVAL.md](docs/EVAL.md) · [PATCH_EVAL.md](docs/PATCH_EVAL.md) ·
[FAILURE_MODES.md](docs/FAILURE_MODES.md)

## Sources

The corpus is built from three sources:

| Source | Content |
|---|---|
| [Pure Data Manual](http://msp.ucsd.edu/Pd_documentation/) (Miller Puckette / UCSD) | Conceptual documentation: patching, messages, signal processing, DSP theory |
| [IEM Object Reference](https://pd.iem.sh/objects/) | Per-object documentation: inlets, outlets, arguments, and descriptions for all vanilla Pd objects |
| [The Theory and Techniques of Electronic Music](http://msp.ucsd.edu/techniques.htm) (Miller Puckette) | Synthesis technique implementation in Pd: FM, waveshaping, filters, delay networks, granular synthesis, and more |

## How It Works

### Architecture

![Architecture: web, CLI and MCP interfaces over one agent core, which drives seven tools against five backends](assets/architecture.svg)

One hand-written loop behind three interfaces and seven tools. The web UI, the
`pdbot` CLI and the MCP server are thin adapters over the same typed event stream;
the [MCP server](#mcp-server) skips the loop and serves the tools themselves. Every tool
call goes through one guard path ([`agent/guardrails.py`](agent/guardrails.py)),
whichever interface made it. A Slack adapter on the same stream is planned.

### Pipeline

```
HTML source files
      ↓
parse_manual.py / parse_object_reference.py / parse_book.py
      ↓
parsed_*.json  (structured sections with metadata)
      ↓
chunk.py  (parent-child chunking, 600-char child chunks with 100-char overlap)
      ↓
child_chunks.json + parent_chunks.json
      ↓
embed_and_index.py  (Voyage AI embeddings → ChromaDB)
      ↓
chroma_db/  +  parent_lookup.json
```

### Agent core

Requests are served by a hand-written tool-use agent in [`agent/`](agent/), not
by a fixed pipeline. The model decides whether to search the documentation, what
to search for, and when it has enough to answer; the loop runs whatever tool calls
come back and feeds the results into the next turn.

The loop emits a typed event stream (`text_delta`, `tool_call`, `tool_result`,
`sources`, `artifact`, `done`) and imports no web framework, so each interface is
a thin adapter: the web endpoint maps those events to SSE, the
[`pdbot` CLI](#cli-pdbot) streams them to the terminal, and the
[MCP server](#mcp-server) serves the tools themselves. A Slack adapter over the
same stream is planned. Tests drive
the loop with a scripted model client, so they need no API key.

`rag.py` still contains the original fixed pipeline (classify → retrieve once →
generate → validate). It is kept as the **baseline arm** of the evaluation so the
agent's numbers can be compared with the pipeline on the same golden set; it is no
longer on the serving path.

### Skills

Skills are versioned playbooks ([Agent Skills](https://code.claude.com/docs/en/skills)
format) the agent can load when a question calls for one, in
[`skills/`](skills/README.md):

```
skills/granular-synthesis/
    SKILL.md          # front matter (name, description, optional version) + instructions
    golden.json       # the questions this skill should improve
    scripts/...       # optional files the instructions refer to
```

Five skills ship: granular synthesis, DSP debugging, abstractions and `$0`
scoping, working from a `.pd` file the user supplies, and Pd flavors (Vanilla vs
Purr Data / Pd-L2Ork / PlugData). Each comes with a small golden set of questions
it should improve, and a version is only served once that slice passes the gate:
CI runs the slice for every changed skill, records the activated content digest
in `skills/activation.json`, and keeps the previous version's snapshot live when
the slice fails. Results are reported per skill in
[`docs/SKILLS.md`](docs/SKILLS.md), and the eval report carries the activated
summary. See [`skills/README.md`](skills/README.md) for the command and the
thresholds.

The model sees only each skill's name and one-line description, in the
`load_skill` tool's description; the instructions arrive as the tool result only
when it calls `load_skill(name)`. That keeps the answer prompt small while the
project carries as many long skills as it likes. Every skill has a version — the
declared one, or a digest of its files — and each run records the versions it
loaded in its `done` event and run log, which is what the per-skill eval gate
keys on.

The registry reads the directory rather than caching it: `ToolRegistry.refresh()`
runs at the start of **every turn**, so activating a skill version takes effect
on the next turn with no restart. Editing a `SKILL.md` is not enough on its own —
the gate has to activate the new digest first (in `$SKILLS_DIR`, default
`skills/`). See
[`skills/README.md`](skills/README.md) for the format and the trust rules.

### Retrieval

At query time, [`agent/retrieval.py`](agent/retrieval.py) runs a hybrid search:

1. **Query classification** — Claude Haiku classifies the query type (`object_reference`, `conceptual`, or `both`), extracts any Pd object names mentioned, and rewrites the query as a standalone search string when conversation history provides context. Agent requests still classify, because the scope gate is a policy decision rather than a tool the model may skip.
2. **Vector search** — Voyage AI embeds the query; ChromaDB finds semantically similar child chunks
3. **BM25 keyword search** — exact token matching against the query, weighted toward object names
4. **Reciprocal Rank Fusion (RRF)** — combines vector and BM25 rankings into a single ranked list
5. **Parent retrieval** — the top child chunks are mapped back to their full parent sections for richer context

In agent mode the model calls this through the `search_docs` tool — it picks the
query, the corpus (`object_reference`, `conceptual` or both) and any exact object
names — and can call it again if the first results are thin. In the pipeline it
runs once, routed by the classifier.

Retrieval quality is measured and regressed in CI — see **[Evaluation Results](docs/EVAL.md)** for current hybrid RRF vs. vector-only vs. BM25-only metrics, plus the agent's tool-call retrieval.

### Response Generation

The agent calls `search_docs`, gets parent sections back as citable excerpts, and
answers with Claude Haiku 5.5 (see [`model_config.py`](model_config.py)) streaming
via Server-Sent Events (SSE). The frontend renders tokens as they arrive. In agent
mode the first answer token lands after the retrieval round trip (~2.1 s measured,
~4 s to a complete answer) because the model searches before it writes; the
single-shot pipeline path measures ~0.36 s to first token. Thinking is disabled for
the answer model, which is what keeps that round trip short (with thinking on, the
same measurement was ~4.7 s to first token). Responses may include:

- Inline `[object~]` references in code spans
- Markdown tables for structured object comparisons
- **Pd patch diagrams** — when a concrete signal chain is described, the model calls the `write_patch` tool with PdPatch JSON; the frontend renders it as an SVG diagram matching the Pd editor's visual style. Patch JSON is validated server-side with Pydantic before it is saved (see [Quality](#quality)).

The SVG renderer supports all standard Pd box types: `obj` (rectangle), `msg` (notched right edge), `floatatom` (corner-cut number box), `comment` (text label), `table`/`array` (named data stores), and UI widgets (`bng`, `tgl`, `hsl`, `vsl`, etc.). Inlet and outlet nubs and patch cords are drawn automatically; layout is computed with [dagre](https://github.com/dagrejs/dagre). Each diagram can be downloaded as a `.pd` file (serialized by [`pd_file.py`](pd_file.py)). Its tests load patches in headless Pd, and are skipped locally when Pd is not installed; set `PD_BIN` to point at a binary that is not on the path. When `PD_SANDBOX_BACKEND` is set, the agent runs the patches it writes, measures their audio and fixes them before answering ([results](docs/PATCH_EVAL.md)). Untrusted patches can be run and rendered to audio in a locked-down container; see [SANDBOX.md](docs/SANDBOX.md).

Patches persist for the whole conversation: a follow-up like "make the filter resonant" loads the previous patch with `read_patch`, edits it with `write_patch`, and replaces it instead of starting over. Stored patches live in the same Postgres/SQLite database as the run log and keep a stable id across revisions. The agent finds them with `list_patches`; `GET /sessions/{session_id}/patches` lists them for a client, and `GET /sessions/{session_id}/patches/{patch_id}.pd` downloads the latest revision. Each diagram's download button uses that URL, so an answer from several turns ago still downloads the newest version, and the chat footer lists every patch in the session.

### Caching

Responses are cached in-process (`cachetools.TTLCache`, 1-hour TTL, keyed by normalised question) so repeated queries return immediately without re-running retrieval or generation. Cache hits are flushed as a single SSE burst.

## Evaluation

A 30-entry golden dataset and automated evaluation harness measure retrieval
quality and guard against regressions.

| Strategy | recall@5 | MRR |
|----------|----------|-----|
| Hybrid RRF | 75% | 0.70 |
| Vector-only | 73% | 0.61 |
| BM25-only | 37% | 0.26 |
| Agent tool calls | 79% | 0.78 |

Retrieval metrics route each question with the dataset's curated `query_type`
rather than the live classifier, so the gate measures the retriever instead of
the router's run-to-run sampling; production routing is covered by the scope
eval below.

Current numbers are published in **[EVAL.md](docs/EVAL.md)**, updated on every push to
`main` by a GitHub Actions workflow.

**Retrieval metrics**: recall@k, MRR, nDCG — with ablation against
vector-only and BM25-only baselines to prove hybrid RRF wins. `--agent-retrieval`
adds a fourth row: what the agent's own `search_docs` calls retrieve for each
entry (one model turn per entry, no answer generation).

**Agent vs pipeline**: the harness runs every model through both arms on the same
entries with `--mode both` — the pipeline retrieves once and generates; the agent
chooses its own `search_docs` calls and is judged against the sections it
retrieved. The report renders a Pipeline vs agent table with mean ± standard
deviation per dimension across trials, the per-dimension deltas, a verdict line
derived from those deltas, and the average tool-call count. The CI gate stays on
the pipeline arm, whose thresholds are calibrated; agent scores are published
beside it.

```bash
# pipeline and agent on the same 10 entries, written to the published report
python -m evaluation.evaluate --generation --mode both --sample 10 --report docs/EVAL.md

# the published comparison: the full 30 entries, 3 trials per arm
python -m evaluation.evaluate --all --mode both --agent-retrieval --sample 30 \
  --trials 3 --report docs/EVAL.md
```

**Measured** (2026-11-24, full 30 entries, both arms, 3 trials, recorded command
above — the tables and the mean ± std are in [EVAL.md](docs/EVAL.md)):

| | Faithfulness | Relevance | Citation | Cost/query | p50 | p95 |
|---|---|---|---|---|---|---|
| Pipeline | 4.63 ± 0.14 | 4.81 ± 0.02 | 4.50 ± 0.09 | $0.0110 | 3.7 s | 5.4 s |
| Agent | 4.61 ± 0.11 | 4.86 ± 0.05 | 4.48 ± 0.10 | $0.0123 | 5.5 s | 7.6 s |

The TASK-5.1 parity conclusion **holds** at 30 entries x 3 trials: the paired
per-trial differences are faithfulness −0.02 ± 0.12, relevance +0.05 ± 0.05 and
citation −0.02 ± 0.02 (agent − pipeline), and none is both larger than its own
spread and at least 0.1, so the honest verdict is "matches" — the agent is level
on faithfulness and citation and a hair ahead on relevance. What the agent buys
is retrieval, not answer quality: its own `search_docs` calls beat the pipeline's
single routed search at 30 entries in the latest run (recall@5 76.1% vs. 75.0%,
recall@10 76.1% vs. 75.0%, MRR 0.703 vs. 0.696, nDCG@5 0.697 vs. 0.692), and they
do it with 1.5 tool calls per question, at +1.8 s p50 / +2.2 s p95 latency and
+$0.0013 per query. The agent-tool-call row is a single-turn, single-run
measurement and is noisy — a one-line prompt change has moved recall@5 from 80.6%
to 76.1% — so read the direction, not the exact points. The measured dollar cost
of that run was **$2.10**. The loop alone does not beat a well-tuned pipeline on
answer quality; what it buys is the mechanism — re-searching, running tools,
self-correcting — that the sandbox and skill subtasks are measured on.

These numbers are post-correction for the judge's inlet-order belief (TASK-5.22),
the agent's object-attribution prompt rules (TASK-5.23) and pd-patch count
canonicalisation (TASK-5.24), so they are not directly comparable to the
pre-correction table; the per-arm change from the TASK-5.21 baseline is pipeline
faithfulness 4.71 → 4.63 and agent 4.66 → 4.61, both inside the observed
single-entry judge noise (unchanged prompts have moved individual entries by up
to two points between runs).

Two caveats are measured, not assumed. The judge's ordering belief was wrong in
both directions — it scored a self-contradicting `[line~]` list example 5/5/5
and a correct left-to-right answer 2 — so the rubric now states Pd's documented
inlet/outlet rules and both directions are pinned as control answers
([`evaluation/judge_controls.py`](evaluation/judge_controls.py)): the reversed
control scores 1–2, the correct one 5, on every run. Single-trial per-entry
scores are still not quoted; the citation dimension in particular swings 2 ↔ 5
for near-identical answers. And the agent's unsupported-specifics gap is now
measured and closed in two steps: the `[unpack]` outlet-order claim is fixed
(warning rate 2/3 before, 0/5 after the prompt rule), and `[snapshot~]`'s
pd-patch count is canonicalised against the retrieved reference
([`agent/patch_counts.py`](agent/patch_counts.py)) — `inlets: 2` appeared in 3/3
answers before, and 0 of 180 answers in the final 30-entry run contains a
`[snapshot~]` count other than 1, with q_006 at 5/5 on five runs.

Each of those is written up, with the measurement that found it and the check
that now stops it recurring, in **[Failure modes the evals caught](docs/FAILURE_MODES.md)**.

**Generation metrics**: faithfulness, answer relevance, and citation
correctness scored by a Claude-as-judge rubric (LLM-as-judge). A sampled run
runs in CI on every push to `main`, so [EVAL.md](docs/EVAL.md) publishes
current generation numbers next to the retrieval tables.

**Model choice**: five models were measured on all 30 entries over 2 trials.
Gated quality was tied within noise, so the answer model is chosen on cost and
citations: `claude-haiku-5-5` now answers, after a citation-hardened prompt
brought its citation score to parity with the previous `claude-sonnet-4-6` at
about a sixth of the production cost per query. See
**[MODEL_COMPARISON.md](docs/MODEL_COMPARISON.md)** for the measurements and the
decision.

**CI gating — retrieval**: `pytest tests/` asserts thresholds (recall@5 ≥ 60%,
MRR ≥ 0.30, hybrid ≥ both baselines). Runs on every PR and push.

**CI gating — generation**: a separate workflow runs a sampled generation eval
on pull requests that touch `agent/**` (the loop and the answer prompts),
`rag.py` (the pipeline baseline), `evaluation/**` (judge rubric, dataset,
harness) or the workflow itself, and fails the build when a mean drops below
threshold.

| Setting | Value |
|---|---|
| Generation model | `claude-haiku-5-5` (set in `model_config.py`) |
| Judge model | `claude-haiku-4-5` (1–5 rubric) |
| Sample | 10 of 30 golden entries, deterministic prefix |
| Gate | faithfulness ≥ 4.0, relevance ≥ 4.5, citation ≥ 4.0 (collapse guard) |
| Deterministic claim checks | 0 errors across both arms (section titles, correct "send them separately" advice and scheme-only URL differences are not errors) |
| Cost bound | **~$0.19 per PR run** (10 entries x 2 arms: 20 generations, ~60 judge calls and 20 scope classifications at measured rates) |

Citation correctness is gated as a collapse guard: the system prompt requires
URLs to be copied verbatim from the retrieved excerpts, which lifted the
30-entry mean from ~4.4 to ~4.5-4.6, and the gate catches a prompt that reintroduces
invented or missing URLs (scores heading toward 3). The 10-entry CI sample is
too small for a tighter citation bound, so the 30-entry comparison remains the
precise measurement. Every run states its sample, both models and the measured
dollars in the GitHub job summary, so cost is visible rather than assumed.
Thresholds and their rationale live in
[`evaluation/gates.py`](evaluation/gates.py).

**Deterministic claim checks** ([`evaluation/claims.py`](evaluation/claims.py))
run on every scored answer, in both arms, next to the judge. They catch what the
judge has demonstrably missed or misjudged: a list that fills inlets right to
left, the recorded wrong claim of a pinned golden entry, an object discussed
without an excerpt covering it, and a URL no excerpt contains. A violation is
deterministic, so it fails the gate for either arm — there is nothing to
calibrate, and repeating the run does not make it go away. A true claim the
excerpts happen not to state is published as a warning instead, because the
model makes that one often enough that gating it would make the build flaky.

```bash
# same command CI runs, against the first 10 entries
python -m evaluation.evaluate --generation --sample 10 --gate --summary /tmp/summary.md
```

The full eval also runs on `main`, and its report is committed back as
`docs/EVAL.md`. Caveat: pull requests from forks do not get repository secrets,
so the generation gate cannot run there and will fail on auth — the retrieval
gate in `eval.yml` still runs (that job needs secrets too).

### From feedback to eval

Every rated answer is stored with its question, answer and retrieved chunks
(see [Feedback loop](#feedback-loop)), so real usage can feed the eval set.
Downvotes are the most valuable input: they are questions the pipeline
actually got wrong.

```bash
# all ratings, or filter by rating and UTC date range
python -m evaluation.export_feedback --rating down --since 2026-11-01 --until 2026-11-30
```

The export writes **`evaluation/feedback_candidates.json`** by default: a
review file, never the golden set. A plain JSON list in the
`golden_dataset*.json` schema, it pre-fills what the stored row can supply:

- `question`, `query_type` — copied from the response record
- `relevant_chunk_ids` — resolved from the stored sources by
  (heading, URL, source) against `parent_lookup.json`; anything that cannot
  be resolved is left out and called out in `notes`
- `difficulty` — `hard` for a downvote, `medium` for an upvote (a human can
  override it)
- `notes` — rating, comment, retrieved sources and the stored answer, plus
  the `message_id` to trace the row back
- `reference_answer` — deliberately empty; a good answer is what the
  reviewer writes

Promotion is manual, by design: the CLI refuses to write any
`golden_dataset*.json` path. Review the candidates, copy the ones worth
keeping into a golden dataset file (for example
`golden_dataset_hard_batch3.json`), then run
`python -m evaluation.evaluate --retrieval`. The review file is gitignored,
because it contains user questions.

Production rows live in Railway Postgres, which has no public proxy, so run
the export from inside the Railway environment (`railway run python -m
evaluation.export_feedback ...`) or point `--database-url` / `DATABASE_URL`
at a dump. The command also works against the local SQLite `feedback.db`.

## Quality

### pd-patch validation

Patch diagram JSON is validated server-side with Pydantic (`pd_schema.py`).
If a block fails validation in the synchronous pipeline `chat()` path, the answer
is retried once with a correction prompt. The streaming paths (the agent, and the
pipeline's SSE arm) replace malformed blocks with a `[patch diagram]` placeholder,
because the answer is already on the wire. Parse failures are tracked via
New Relic (`Custom/PdPatch/ParseFailure`) and Langfuse (`pd_patch_valid`
score); validated patches are also emitted as `artifact` events so an interface
can render the diagram without re-parsing the prose.

Inlet/outlet counts are canonicalised against the retrieved object reference
before validation ([`agent/patch_counts.py`](agent/patch_counts.py)): a count that
contradicts a fixed reference count is rewritten (for example `[snapshot~]`'s
single inlet), while variable-arity objects (`[pack]`, `[dac~]`) and objects the
excerpts do not cover are left alone, as is any object whose connections would
fall outside the repaired count. The eval applies the same canonicalisation to
both arms, and repairs are counted under
`Custom/PdPatch/CountRepaired`.

### Scope gate

Questions outside Pure Data are refused before generation, and in-domain harmful
requests are declined, using the classification call the request already makes
once per query (see [`scope.py`](scope.py)). The agent runs this gate before its
loop, because a refusal is a policy decision rather than a tool the model may
skip:

- **Off-topic** — "What is the capital of France?" gets a short refusal naming Pd
  and suggesting Pd topics. No retrieval, no generation call.
- **Mixed** — "What does [osc~] do, and what is the capital of France?" answers the
  Pd part and declines the rest in the same reply: the classifier extracts a
  Pd-only query for retrieval and adds a scope note to the generation prompt.
- **Policy violation** — including Pd-framed harmful requests such as "a patch
  that overloads a sound system to damage hearing" — gets a policy refusal and no
  generation. The system prompt also carries an explicit policy instruction, so a
  case the classifier misses is still declined by the model.
- **Low relevance** — hybrid retrieval always returns its top-k, so a cutoff on
  the best cosine similarity (0.42) makes both the pipeline's retrieval step and
  the agent's `search_docs` tool return nothing for a query with no real match
  (the agent then says the docs don't cover it, and must not invent a URL).
  Measured against the
  golden set: the lowest legitimate question scores 0.449 (p10 0.504) while 12
  unrelated probes top out at 0.433 (median 0.297), so the cutoff sits 0.03 below
  the lowest legitimate query.

Scope is judged from the question itself, with history used only to resolve
references, so on-topic history cannot launder an off-topic question; two
multi-turn cases in the eval set cover that. Refusals are counted in New Relic
(`Custom/Guard/OutOfScope`, `Custom/Guard/LowRelevance`) and scored in Langfuse,
so the block rate is visible.

The cases live in
[`evaluation/scope_dataset.json`](evaluation/scope_dataset.json) — 20 of them
(6 off-topic, 3 mixed, 6 policy, 2 multi-turn bypass, 3 on-topic) — and are scored
by `evaluation/scope_eval.py` with one classifier call each and no generation.
The CI gate fails below 90% adherence; it currently scores 100%.

**Limits**: this is a guardrail, not a security boundary. The classifier can be
wrong in both directions — unknown labels fail open to in-scope, because refusing
a legitimate Pd question is worse than answering a borderline one — and a
determined user can still disguise an off-topic request as Pd work. There is no
output moderation: harmful in-domain output the model does produce is caught only
by the faithfulness guard and Claude's own training.

### Feedback loop

Every assistant response is stored when it finishes streaming (question,
answer, retrieved sources, query type) under its own random `message_id`,
so two users asking the same question get separate records. Thumbs up/down
buttons in the chat UI submit cryptographically-signed feedback
(`POST /feedback`), which is stored against that response row. Ratings
therefore keep their full context even after the in-memory response cache
expires. The data grows an eval set from real usage — see
[From feedback to eval](#from-feedback-to-eval).

### Cost per request

Every request prices its model calls: classification, generation and the inline
faithfulness guard each attach a USD cost to their Langfuse observation, and the
request trace carries the total plus a per-kind breakdown.
[`pricing.py`](pricing.py) is the single rate table shared with the evaluation
harness, so the cost the eval reports per query and the cost production reports
per request are directly comparable.

Measured on 2026-11-24 (five live requests through the streaming path, current
models and rates):

| | per request | per 1 000 requests |
|---|---|---|
| **Total** | **$0.0048** | **$4.80** |
| — faithfulness guard | $0.0035 (≈74%) | $3.5 |
| — generation | $0.0008 (≈16%) | $0.8 |
| — classification | $0.0005 (≈10%) | $0.5 |
| Prompt-cache savings | $0.000095 (≈2%) | $0.095 |

The guard dominates the bill: it re-reads the retrieved excerpts to judge
faithfulness, so its input is several times the answer's. That is the first place
to look for cost reductions (for example judging only low-confidence answers, or
sending the judge fewer chunks). Cache savings are small by construction — only
the ~800-token system prompt carries `cache_control`; the documentation excerpts
differ per query and cannot be cached.

New Relic receives `Custom/Cost/RequestUsd` and `Custom/Cost/CacheSavingsUsd`
per request; Langfuse shows the same figures on the trace and on each model call.

### API auth

The backend accepts multiple API keys (`CHAT_API_KEYS`, comma-separated in
env), enabling zero-downtime rotation. The `/feedback` endpoint additionally
requires an HMAC-SHA256 signature over the payload, preventing replay attacks
(60-second window).

## Stack

**Backend**
- [FastAPI](https://fastapi.tiangolo.com/) + uvicorn
- [ChromaDB](https://www.trychroma.com/) — vector store (embedded, file-backed)
- [Voyage AI](https://www.voyageai.com/) — embeddings (`voyage-3-lite`)
- [Anthropic Claude](https://www.anthropic.com/) — classification (`claude-haiku-4-5`) + generation (`claude-sonnet-4-6`)
- [rank-bm25](https://github.com/dorianbrown/rank_bm25) — keyword search
- slowapi — rate limiting (10/min `/chat`, 30/min `/feedback`, `/patch/pd` and session-patch downloads, 60/min the artifact and session-patch list endpoints)
- New Relic APM — request tracing, external call instrumentation (Anthropic, Voyage AI), cache hit/miss metrics, pd-patch validation failure tracking
- [Langfuse](https://langfuse.com) (self-hosted on Railway) — LLM-native tracing: token accounting (input/output/cache-read), trace trees (classify → embed → retrieve → generate), cost attribution
- Postgres (Railway) via SQLAlchemy — response and feedback store, event-sourced run log, and per-session patches; falls back to a local SQLite file (`feedback.db`) when `DATABASE_URL` is unset

**Frontend**
- React 19 + TypeScript + Vite
- Tailwind CSS v4 + shadcn/ui
- [dagre](https://github.com/dagrejs/dagre) — directed graph layout for patch diagrams
- SVG patch renderer — renders Pd-style object boxes, inlet/outlet nubs, and patchcords
- New Relic Browser — page performance, AJAX tracking, JS error monitoring

## Deployment

The application is deployed as two separate services:

| Layer | Platform | Config |
|---|---|---|
| Backend (FastAPI) | [Railway](https://railway.app) | `.railway/railway.ts` |
| Frontend (React/Vite static build) | [Vercel](https://vercel.com) | `frontend/vercel.json` |

The Vercel deployment rewrites `/api/*` to the Railway backend URL server-side, so the browser never makes cross-origin requests and CORS is not required for production.

Railway runs `embed_and_index.py` as part of the build step to construct ChromaDB from the committed chunk files (`child_chunks.json`, `parent_chunks.json`). ChromaDB itself is not committed — it is rebuilt on each deploy.

Responses and feedback are stored in a Railway Postgres service in the same project. The backend service's `DATABASE_URL` variable is set to the reference `${{Postgres.DATABASE_URL}}`, which resolves to Postgres over Railway's private network. Tables are created on startup. The container filesystem is wiped on every deploy, so production must not run without `DATABASE_URL`; the backend logs a warning if it does.

### CI/CD

A [GitHub Actions workflow](.github/workflows/eval.yml) runs on every push and
PR:

1. Builds ChromaDB from committed chunk files
2. Runs the retrieval evaluation (recall@k, MRR, nDCG) — plus, on `main`, the
   agent's tool-call retrieval and a sampled pipeline-vs-agent generation eval —
   writing `docs/EVAL.md`
3. Runs `pytest tests/` with threshold assertions
4. Publishes `docs/EVAL.md` to `main` (skips on PRs, and if the code on `main` changed during the run)

Pushes to `main` that change only `backlog/` or Markdown files skip it.

A second workflow
([`generation-eval.yml`](.github/workflows/generation-eval.yml)) adds the
generation gate on pull requests that touch `agent/**`, `rag.py`,
`evaluation/**` or itself, failing the build below the documented
faithfulness/relevance thresholds and reporting the measured cost in the job
summary. Both arms run so a change under `agent/` is measured by the arm it
changes; the threshold check stays on the pipeline arm until the agent has its
own calibrated baseline.

Requires `ANTHROPIC_API_KEY` and `VOYAGE_API_KEY` set as [repository secrets](https://github.com/wilsontr/pd-chatbot/settings/secrets/actions).

## Setup

### Prerequisites

- Python 3.11+
- Node.js 18+
- Voyage AI API key
- Anthropic API key

### Backend

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` file:

```
VOYAGE_API_KEY=...
ANTHROPIC_API_KEY=...
CHAT_API_KEYS=...            # comma-separated for rotation; e.g. "key1,key2"
ALLOWED_ORIGIN=http://localhost:5173
# Optional — response/feedback store; defaults to a local SQLite file (feedback.db)
DATABASE_URL=postgresql://user:pass@host:5432/db
# Optional — Langfuse LLM observability
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_BASE_URL=http://localhost:3000
```

### Build the corpus

`child_chunks.json`, `parent_chunks.json`, and `parent_lookup.json` are committed to the repo and are sufficient to run `embed_and_index.py` directly. Re-parsing from source is only needed if you modify the source documents or parsing logic.

```bash
# Re-parse source documents (optional — only if modifying parse logic)
python parse_manual.py
python parse_object_reference.py
python parse_book.py          # requires puckette_book/ local mirror

# Chunk (optional — only if re-parsing above)
python chunk.py

# Index — always required on first run or after re-chunking
python embed_and_index.py     # makes ~20 Voyage AI API calls, writes chroma_db/
```

The `puckette_book/` directory should contain a local mirror of the HTML edition of *The Theory and Techniques of Electronic Music*, available at `http://msp.ucsd.edu/techniques.htm`. It is gitignored and only needed if re-running `parse_book.py`.

### Run

```bash
# Backend
uvicorn main:app --reload

# Frontend
cd frontend && npm install && npm run dev
```

The frontend proxies `/api` to `http://localhost:8000` (configured in `vite.config.ts`).

![The chat interface: a streamed answer with Pd patch diagrams rendered as SVG](assets/chatbot.png)

### CLI (`pdbot`)

`pdbot` is the terminal adapter over the same agent core, sandbox runner, and run
store. Install the project in editable mode to put `pdbot` on your `PATH`:

```bash
pip install -e . --no-deps
```

Usage:

```bash
# Stream an answer with live tool steps on stderr
pdbot ask "how does osc~ work?"

# Ask and write any generated patches (and rendered audio) to ./patches/
pdbot ask "build a 440 Hz FM bell patch" -o ./patches

# Continue a previous session's patch workspace
pdbot ask "add a low-pass filter to it" --session <session_id> -o ./patches

# Run a local .pd file in the headless Pd sandbox (and optionally save the WAV)
pdbot run ./patches/fm-bell.pd --seconds 3 --wav ./out.wav

# List recent stored runs and replay one (optionally exporting its .pd files)
pdbot runs
pdbot replay <run_id> -o ./replayed-patches
```

`pdbot run` and `pdbot replay` do not touch the vector index or call an LLM, so
they work without API keys. `pdbot ask` reads `.env` and uses the same
`RunStore` / `SessionPatchStore` defaults as the backend (pass `--no-log` to
skip database persistence or `--db` to point at a specific database URL).

### MCP server

The agent's tools are also published as an [MCP](https://modelcontextprotocol.io)
server, so Claude Code, Claude Desktop or any other MCP client can search the Pd
documentation and build patches with its own model. It needs the same `.env`
(Voyage key) and built `chroma_db/` as the backend, and runs over stdio.

| Tool | Offered |
|---|---|
| `search_docs` | always |
| `list_patches`, `read_patch`, `write_patch` | always; `write_patch` returns the `.pd` file |
| `load_skill` | always |
| `run_patch`, `analyze_audio` | only when `PD_SANDBOX_BACKEND` is set ([docs/SANDBOX.md](docs/SANDBOX.md)) |

Calls go through the same guardrails as the web agent
([`agent/guardrails.py`](agent/guardrails.py) `run_guarded_tool`): the `mcp`
interface tool policy, tool-output prompt-injection screening, and the patch
tools' limits (12 patches and 6 sandbox runs per server session, runs of at most
10 s). One server process is one session; restarting it clears its patches.

**Claude Code:**

```bash
claude mcp add pd-chatbot -- /path/to/pd-chatbot/venv/bin/python /path/to/pd-chatbot/mcp_server.py
# with patch execution:
claude mcp add pd-chatbot -e PD_SANDBOX_BACKEND=docker -- /path/to/pd-chatbot/venv/bin/python /path/to/pd-chatbot/mcp_server.py
```

**Claude Desktop** (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "pd-chatbot": {
      "command": "/path/to/pd-chatbot/venv/bin/python",
      "args": ["/path/to/pd-chatbot/mcp_server.py"],
      "env": { "PD_SANDBOX_BACKEND": "docker" }
    }
  }
}
```

Drop the `env` entry to leave patch execution off. Other clients take the same
command and arguments. Use absolute paths: [`mcp_server.py`](mcp_server.py)
moves to the repo root itself, so it works from whatever directory the client
starts it in. To try it without a client, use the MCP Inspector:
`npx @modelcontextprotocol/inspector venv/bin/python mcp_server.py`.

### Observability (optional)

There are two layers:

**New Relic APM** — request tracing, external call instrumentation, custom
metrics. Inactive without credentials. To enable:

**Backend (Railway env vars):**
```
NEW_RELIC_LICENSE_KEY=...
NEW_RELIC_APP_NAME=pd-chatbot-backend
NEW_RELIC_LOG=stdout
NEW_RELIC_LOG_LEVEL=info
```

**Frontend (Vercel env vars):**
```
VITE_NR_LICENSE_KEY=...       # browser license key (from NR snippet)
VITE_NR_APP_ID=...
VITE_NR_ACCOUNT_ID=...
```

The Railway start command (`.railway/railway.ts`) uses `newrelic-admin run-program` to bootstrap the Python APM agent. The frontend initialises `BrowserAgent` in `src/main.jsx`, guarded by `VITE_NR_LICENSE_KEY` so local dev without credentials is unaffected.

**Langfuse** (LLM-native tracing) — token counts, trace trees, cost attribution.
Self-hosted on Railway via the [Langfuse v3 template](https://langfuse.com/self-hosting/deployment/railway).
Set these env vars on the backend:

```
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_BASE_URL=https://langfuse-web-production-xxxx.up.railway.app
```

When unset, the system degrades gracefully — all observations become no-ops.
