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

### Response Generation

Retrieved chunks are passed as context to Claude Sonnet, which streams a response grounded in the documentation via Server-Sent Events (SSE). The frontend begins rendering tokens as they arrive, so answers start appearing within ~1 second of retrieval completing. Responses may include:

- Inline `[object~]` references in code spans
- Markdown tables for structured object comparisons
- **Pd patch diagrams** — when a concrete signal chain is described, the model emits a `pd-patch` JSON block that the frontend renders as an SVG diagram matching the Pd editor's visual style

The SVG renderer supports all standard Pd box types: `obj` (rectangle), `msg` (notched right edge), `floatatom` (corner-cut number box), `comment` (text label), `table`/`array` (named data stores), and UI widgets (`bng`, `tgl`, `hsl`, `vsl`, etc.). Inlet and outlet nubs and patch cords are drawn automatically; layout is computed with [dagre](https://github.com/dagrejs/dagre).

### Caching

Responses are cached in-process (`cachetools.TTLCache`, 1-hour TTL, keyed by normalised question) so repeated queries return immediately without re-running retrieval or generation. Cache hits are flushed as a single SSE burst.

## Stack

**Backend**
- [FastAPI](https://fastapi.tiangolo.com/) + uvicorn
- [ChromaDB](https://www.trychroma.com/) — vector store (embedded, file-backed)
- [Voyage AI](https://www.voyageai.com/) — embeddings (`voyage-3-lite`)
- [Anthropic Claude](https://www.anthropic.com/) — classification (`claude-haiku-4-5`) + generation (`claude-sonnet-4-6`)
- [rank-bm25](https://github.com/dorianbrown/rank_bm25) — keyword search
- slowapi — rate limiting (10 requests/minute per IP)
- New Relic APM — request tracing, external call instrumentation (Anthropic, Voyage AI), cache hit/miss metrics

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
CHAT_API_KEY=...          # shared secret for the /chat endpoint
ALLOWED_ORIGIN=http://localhost:5173
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

New Relic instrumentation is included but inactive without credentials. To enable:

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
