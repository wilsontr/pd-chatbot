# Pd Documentation Assistant

A retrieval-augmented generation (RAG) chatbot for answering questions about [Pure Data](http://msp.ucsd.edu/Pd_documentation/) (Pd) — a visual programming language for audio and multimedia. The assistant draws on the official Pd documentation and a synthesis textbook to answer questions about objects, patching concepts, audio signal processing, and synthesis techniques.

![Pd Documentation Assistant](assets/chatbot.png)

## Sources

The corpus is built from three sources:

| Source | Content |
|---|---|
| [Pure Data Manual](http://msp.ucsd.edu/Pd_documentation/) (Miller Puckette / UCSD) | Conceptual documentation: patching, messages, signal processing, DSP theory |
| [IEM Object Reference](https://pd.iem.sh/objects/) | Per-object documentation: inlets, outlets, arguments, and descriptions for all vanilla Pd objects |
| [The Theory and Techniques of Electronic Music](http://msp.ucsd.edu/techniques.htm) (Miller Puckette) | Synthesis technique implementation in Pd: FM, waveshaping, filters, delay networks, granular synthesis, and more |

## How It Works

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

### Retrieval

At query time, `rag.py` runs a hybrid search:

1. **Query classification** — Claude Haiku classifies the query type (`object_reference`, `conceptual`, or `both`), extracts any Pd object names mentioned, and rewrites the query as a standalone search string when conversation history provides context
2. **Vector search** — Voyage AI embeds the (rewritten) query; ChromaDB finds semantically similar child chunks
3. **BM25 keyword search** — exact token matching against the original query, weighted toward object names
4. **Reciprocal Rank Fusion (RRF)** — combines vector and BM25 rankings into a single ranked list
5. **Parent retrieval** — the top child chunks are mapped back to their full parent sections for richer context

Retrieval quality is measured and regressed in CI — see **[Evaluation Results](docs/EVAL.md)** for current hybrid RRF vs. vector-only vs. BM25-only metrics.

### Response Generation

Retrieved chunks are passed as context to Claude Sonnet, which streams a response grounded in the documentation via Server-Sent Events (SSE). The frontend begins rendering tokens as they arrive, so answers start appearing within ~1 second of retrieval completing. Responses may include:

- Inline `[object~]` references in code spans
- Markdown tables for structured object comparisons
- **Pd patch diagrams** — when a concrete signal chain is described, the model emits a `pd-patch` JSON block that the frontend renders as an SVG diagram matching the Pd editor's visual style. Patch JSON is validated server-side with Pydantic and retried on failure (see [Quality](#quality)).

The SVG renderer supports all standard Pd box types: `obj` (rectangle), `msg` (notched right edge), `floatatom` (corner-cut number box), `comment` (text label), `table`/`array` (named data stores), and UI widgets (`bng`, `tgl`, `hsl`, `vsl`, etc.). Inlet and outlet nubs and patch cords are drawn automatically; layout is computed with [dagre](https://github.com/dagrejs/dagre).

### Caching

Responses are cached in-process (`cachetools.TTLCache`, 1-hour TTL, keyed by normalised question) so repeated queries return immediately without re-running retrieval or generation. Cache hits are flushed as a single SSE burst.

## Evaluation

A 30-entry golden dataset and automated evaluation harness measure retrieval
quality and guard against regressions.

| Strategy | recall@5 | MRR |
|----------|----------|-----|
| Hybrid RRF | 80% | 0.76 |
| Vector-only | 73% | 0.61 |
| BM25-only | 37% | 0.26 |

Current numbers are published in **[EVAL.md](docs/EVAL.md)**, updated on every push to
`main` by a GitHub Actions workflow.

**Retrieval metrics**: recall@k, MRR, nDCG — with ablation against
vector-only and BM25-only baselines to prove hybrid RRF wins.

**Generation metrics**: faithfulness, answer relevance, and citation
correctness scored by a Claude-as-judge rubric (LLM-as-judge). A sampled run
runs in CI on every push to `main`, so [EVAL.md](docs/EVAL.md) publishes
current generation numbers next to the retrieval tables.

**CI gating — retrieval**: `pytest tests/` asserts thresholds (recall@5 ≥ 60%,
MRR ≥ 0.30, hybrid ≥ both baselines). Runs on every PR and push.

**CI gating — generation**: a separate workflow runs a sampled generation eval
on pull requests that touch `rag.py` (the system prompt lives there),
`evaluation/**` (judge rubric, dataset, harness) or the workflow itself, and
fails the build when a mean drops below threshold.

| Setting | Value |
|---|---|
| Generation model | `claude-sonnet-4-6` |
| Judge model | `claude-haiku-4-5` (1–5 rubric) |
| Sample | 10 of 30 golden entries, deterministic prefix |
| Gate | faithfulness ≥ 4.0, relevance ≥ 4.5 |
| Cost bound | ~$0.031/query measured → **~$0.32 per PR run** (generation + judge tokens; retrieval calls are extra) |

Citation correctness is reported but not gated: it is the noisiest dimension
(baseline mean 4.6 with a minimum of 2). Every run states its sample, both
models and the measured dollars in the GitHub job summary, so cost is visible
rather than assumed. Thresholds and their rationale live in
[`evaluation/gates.py`](evaluation/gates.py).

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
If a block fails validation in the synchronous `chat()` path, the answer is
retried once with a correction prompt. The streaming path replaces malformed
blocks with a `[patch diagram]` placeholder. Parse failures are tracked via
New Relic (`Custom/PdPatch/ParseFailure`) and Langfuse (`pd_patch_valid`
score).

### Feedback loop

Every assistant response is stored when it finishes streaming (question,
answer, retrieved sources, query type) under its own random `message_id`,
so two users asking the same question get separate records. Thumbs up/down
buttons in the chat UI submit cryptographically-signed feedback
(`POST /feedback`), which is stored against that response row. Ratings
therefore keep their full context even after the in-memory response cache
expires. The data grows an eval set from real usage — see
[From feedback to eval](#from-feedback-to-eval).

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
- slowapi — rate limiting (10/min `/chat`, 30/min `/feedback`)
- New Relic APM — request tracing, external call instrumentation (Anthropic, Voyage AI), cache hit/miss metrics, pd-patch validation failure tracking
- [Langfuse](https://langfuse.com) (self-hosted on Railway) — LLM-native tracing: token accounting (input/output/cache-read), trace trees (classify → embed → retrieve → generate), cost attribution
- Postgres (Railway) via SQLAlchemy — response and feedback store; falls back to a local SQLite file (`feedback.db`) when `DATABASE_URL` is unset

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
| Backend (FastAPI) | [Railway](https://railway.app) | `railway.json` |
| Frontend (React/Vite static build) | [Vercel](https://vercel.com) | `frontend/vercel.json` |

The Vercel deployment rewrites `/api/*` to the Railway backend URL server-side, so the browser never makes cross-origin requests and CORS is not required for production.

Railway runs `embed_and_index.py` as part of the build step to construct ChromaDB from the committed chunk files (`child_chunks.json`, `parent_chunks.json`). ChromaDB itself is not committed — it is rebuilt on each deploy.

Responses and feedback are stored in a Railway Postgres service in the same project. The backend service's `DATABASE_URL` variable is set to the reference `${{Postgres.DATABASE_URL}}`, which resolves to Postgres over Railway's private network. Tables are created on startup. The container filesystem is wiped on every deploy, so production must not run without `DATABASE_URL`; the backend logs a warning if it does.

### CI/CD

A [GitHub Actions workflow](.github/workflows/eval.yml) runs on every push and
PR:

1. Builds ChromaDB from committed chunk files
2. Runs the retrieval evaluation (recall@k, MRR, nDCG) — and, on `main`, a
   sampled generation eval — writing `docs/EVAL.md`
3. Auto-commits `docs/EVAL.md` to `main` (skips on PRs)
4. Runs `pytest tests/` with threshold assertions

A second workflow
([`generation-eval.yml`](.github/workflows/generation-eval.yml)) adds the
generation gate on pull requests that touch `rag.py`, `evaluation/**` or itself,
failing the build below the documented faithfulness/relevance thresholds and
reporting the measured cost in the job summary.

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

The Railway start command (`railway.json`) uses `newrelic-admin run-program` to bootstrap the Python APM agent. The frontend initialises `BrowserAgent` in `src/main.jsx`, guarded by `VITE_NR_LICENSE_KEY` so local dev without credentials is unaffected.

**Langfuse** (LLM-native tracing) — token counts, trace trees, cost attribution.
Self-hosted on Railway via the [Langfuse v3 template](https://langfuse.com/self-hosting/deployment/railway).
Set these env vars on the backend:

```
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_BASE_URL=https://langfuse-web-production-xxxx.up.railway.app
```

When unset, the system degrades gracefully — all observations become no-ops.
