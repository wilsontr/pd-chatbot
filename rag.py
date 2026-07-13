# rag.py
import asyncio
import hashlib
import json
import logging
import re
import threading
from typing import Any
import newrelic.agent
import chromadb
import cachetools
from dotenv import load_dotenv
load_dotenv()
import voyageai
from rank_bm25 import BM25Okapi
import anthropic
from langfuse import get_client as _get_langfuse

logger = logging.getLogger(__name__)

# Type aliases for the dict shapes that flow through the pipeline
HistoryItem = dict[str, str]   # {"role": "user"|"assistant", "content": str}
Chunk = dict[str, Any]         # parent chunk from parent_lookup

# --- Langfuse observability (LLM-native tracing) ---
try:
    _langfuse = _get_langfuse()
    if not _langfuse.auth_check():
        _langfuse = None
except Exception:
    _langfuse = None
if _langfuse is None:
    logger.info("langfuse not configured — LLM tracing disabled")
else:
    logger.info("langfuse connected")


class _NoopObs:
    """Stand-in when Langfuse is unavailable. Supports update/end/__enter__/__exit__."""

    def update(self, **kwargs: Any) -> None: pass
    def end(self) -> None: pass
    def __enter__(self) -> "_NoopObs": return self
    def __exit__(self, *a: Any) -> None: pass


def _start_obs(name: str, as_type: str = "span", **kwargs: Any) -> Any:
    if _langfuse is None:
        return _NoopObs()
    return _langfuse.start_observation(as_type=as_type, name=name, **kwargs)


def _start_obs_ctx(name: str, as_type: str = "span", **kwargs: Any) -> Any:
    """Context-manager observation; sets as current for nested-child propagation."""
    if _langfuse is None:
        return _NoopObs()
    return _langfuse.start_as_current_observation(as_type=as_type, name=name, **kwargs)


def _token_usage(resp: Any) -> dict[str, int] | None:
    """Extract token counts from an Anthropic response (sync or stream final)."""
    try:
        u = resp.usage
        return {
            "input": u.input_tokens,
            "output": u.output_tokens,
            "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
            "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0,
            "total": u.input_tokens + u.output_tokens,
        }
    except Exception:
        return None

_response_cache: cachetools.TTLCache = cachetools.TTLCache(maxsize=256, ttl=3600)
_cache_lock = threading.Lock()

HISTORY_COMPRESS_AFTER = 8   # items (4 turns); older turns are summarized
HISTORY_MAX_OUTGOING = 18    # cap returned history so the next request passes Pydantic max_length=20

# --- Clients and indexes (initialized once at startup) ---
vc = voyageai.Client()
db = chromadb.PersistentClient(path="./chroma_db")
collection = db.get_collection("pd_docs")
llm = anthropic.Anthropic()
async_llm = anthropic.AsyncAnthropic()

with open("child_chunks.json") as f:
    child_chunks: list[Chunk] = json.load(f)
with open("parent_lookup.json") as f:
    parent_lookup: dict[str, Chunk] = json.load(f)

# Build BM25 index over all child chunks for keyword search
tokenized_corpus = [c["text"].lower().split() for c in child_chunks]
bm25 = BM25Okapi(tokenized_corpus)

# --- System prompt (module-level constant; edit here to tune LLM behaviour) ---
SYSTEM_PROMPT = (
    "You are a helpful assistant that answers questions about Pure Data (Pd), "
    "a visual programming language for music and multimedia. "
    "Use the provided documentation excerpts as your primary source. "
    "You may draw on broader knowledge of Pd to explain concepts or suggest implementation "
    "strategies, but ground your answer in the documentation where it is relevant. "
    "If the documentation contradicts general knowledge, prefer the documentation. "
    "Include relevant object names and brief examples where helpful. "
    "Cite the source URLs at the end of your answer.\n\n"
    "Formatting rules:\n"
    "- Write individual Pd object names inline as `[osc~]` (backtick code spans).\n"
    "- When your answer includes a concrete patch example or signal chain (two or more "
    "connected objects), emit a fenced code block with language tag pd-patch containing "
    "a JSON object describing the patch. Use this exact schema:\n"
    "  {\"objects\": [{\"id\": \"0\", \"type\": \"obj\", \"text\": \"osc~ 440\", \"inlets\": 2, \"outlets\": 1}, ...],\n"
    "   \"connections\": [{\"srcId\": \"0\", \"srcOutlet\": 0, \"dstId\": \"1\", \"dstInlet\": 0}, ...]}\n"
    "  type is one of: obj (object box — named objects like osc~, pack, expr~), "
    "msg (message box — literal values or messages like `0`, `bang`, `0 4 2000`; created with Ctrl+2 in Pd), "
    "floatatom (number box — displays/sends a float value), "
    "comment (plain text label, no inlets or outlets). "
    "inlets and outlets must reflect the actual Pd object counts. "
    "Keep patch diagrams to 15 objects or fewer; for complex patches, show only the key signal path and describe the rest in prose. "
    "Place the pd-patch block immediately after the prose that describes it. "
    "Omit the pd-patch block when merely mentioning individual objects without a connection.\n"
    "- When describing a signal chain in prose (not a patch block), use a markdown table "
    "with columns Object and Role, or a numbered list — never multiple objects separated by | on one line."
)


def _make_cache_key(question: str) -> str:
    # Key on the normalized question only, not history. Documentation answers are
    # objective — the same question asked mid-session should return the cached answer
    # rather than paying full retrieval + LLM cost again.
    return hashlib.sha256(question.strip().lower().encode()).hexdigest()


def _compress_history(history: list[HistoryItem]) -> list[HistoryItem]:
    """Summarize turns older than HISTORY_COMPRESS_AFTER items using Haiku."""
    if len(history) <= HISTORY_COMPRESS_AFTER:
        return history

    recent = history[-HISTORY_COMPRESS_AFTER:]
    old = history[:-HISTORY_COMPRESS_AFTER]

    old_text = "\n".join(
        f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content'][:400]}"
        for m in old
    )
    obs = _start_obs_ctx("compress_history", as_type="generation",
                         model="claude-haiku-4-5", input=old_text[:500])
    with obs as gen:
        with newrelic.agent.ExternalTrace('anthropic', 'api.anthropic.com', 'POST'):
            resp = llm.messages.create(
                model="claude-haiku-4-5",
                max_tokens=200,
                messages=[{
                    "role": "user",
                    "content": (
                        "Summarize this Pure Data chatbot conversation in 2-3 sentences, "
                        "capturing key topics and important context:\n\n" + old_text
                    )
                }]
            )
        gen.update(output=resp.content[0].text, usage_details=_token_usage(resp))
    summary = resp.content[0].text.strip()
    logger.info("compressed %d history items into summary", len(old))

    return [
        {"role": "user", "content": f"[Conversation summary: {summary}]"},
        {"role": "assistant", "content": "Understood."},
    ] + recent


@newrelic.agent.function_trace()
def classify_query(question: str, history: list[HistoryItem]) -> dict[str, Any]:
    """
    Classify the query type and extract object names.
    Also rewrites the question as a standalone search query
    if there is conversation history that provides context.
    """
    history_text = ""
    if history:
        recent = history[-4:]
        history_text = "\n".join([
            f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content'][:200]}"
            for m in recent
        ])

    prompt = f"""You are helping route questions about Pure Data (Pd) to the right documentation source.

{"Conversation history:\n" + history_text if history_text else ""}

Question: {question}

Respond in JSON with these fields:
- "rewritten_query": standalone version of the question incorporating any context from history
- "query_type": one of "object_reference", "conceptual", or "both"
- "object_names": list of Pd object names mentioned (e.g. ["osc~", "pack"]), empty list if none

Return only the JSON object, no other text."""

    obs_cm = _start_obs_ctx("classify_query", as_type="generation",
                            model="claude-haiku-4-5", input=question)
    with obs_cm as obs:
        with newrelic.agent.ExternalTrace('anthropic', 'api.anthropic.com', 'POST'):
            response = llm.messages.create(
                model="claude-haiku-4-5",
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}]
            )
        raw = response.content[0].text.strip()
        raw = re.sub(r'^```(?:json)?\s*\n', '', raw)
        raw = re.sub(r'\n```\s*$', '', raw)
        raw = raw.strip()
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("classify_query returned unparseable JSON: %r", raw)
            result = {
                "rewritten_query": question,
                "query_type": "both",
                "object_names": []
            }
        obs.update(output=result, usage_details=_token_usage(response))
    return result


def vector_search(
    query: str,
    top_k: int = 10,
    content_type: str | None = None,
    object_name: str | None = None,
    query_vector: list[float] | None = None,
) -> tuple[list[str], list[dict]]:
    """Search ChromaDB with optional metadata filters."""
    if query_vector is None:
        with newrelic.agent.ExternalTrace('voyageai', 'api.voyageai.com', 'POST'):
            query_vector = vc.embed([query], model="voyage-3-lite").embeddings[0]

    filters = []
    if content_type:
        filters.append({"content_type": {"$eq": content_type}})
    if object_name:
        filters.append({"object_name": {"$eq": object_name}})
    if len(filters) == 1:
        where = filters[0]
    elif len(filters) > 1:
        where = {"$and": filters}
    else:
        where = None

    results = collection.query(
        query_embeddings=[query_vector],
        n_results=top_k,
        where=where,
        include=["metadatas", "distances"]
    )
    return results["ids"][0], results["metadatas"][0]


def bm25_search(query: str, top_k: int = 10, content_type: str | None = None) -> list[str]:
    """BM25 keyword search — especially good for exact Pd object names."""
    tokenized_query = query.lower().split()
    scores = bm25.get_scores(tokenized_query)

    filtered = [
        (i, scores[i]) for i, c in enumerate(child_chunks)
        if content_type is None or c["content_type"] == content_type
    ]
    filtered.sort(key=lambda x: x[1], reverse=True)
    top_indices = [i for i, _ in filtered[:top_k]]
    return [child_chunks[i]["id"] for i in top_indices]


def hybrid_retrieve(
    query: str,
    top_k: int = 5,
    content_type: str | None = None,
    object_names: list[str] | None = None,
    bm25_query: str | None = None,
    query_vector: list[float] | None = None,
) -> list[Chunk]:
    """
    Combine vector search and BM25 via RRF, return top-k by rank.
    If specific object names are mentioned, also do exact lookups.
    bm25_query defaults to query but can be set to the original user question
    to preserve exact object name tokens (e.g. osc~) that a rewritten query may drop.
    query_vector may be passed in from retrieve() to avoid redundant Voyage AI calls.
    """
    if bm25_query is None:
        bm25_query = query

    # If a specific object name is mentioned, always include its exact entry
    exact_results = []
    if object_names:
        for name in object_names:
            exact_ids, exact_metas = vector_search(
                query, top_k=1,
                content_type="object_reference",
                object_name=name,
                query_vector=query_vector,
            )
            for id_, meta in zip(exact_ids, exact_metas):
                parent = parent_lookup.get(meta["parent_id"])
                if parent:
                    exact_results.append(parent)

    # Broad vector + BM25 search
    v_ids, v_metas = vector_search(query, top_k=top_k * 2, content_type=content_type, query_vector=query_vector)
    k_ids = bm25_search(bm25_query, top_k=top_k * 2, content_type=content_type)

    # Reciprocal rank fusion
    combined = {}
    for rank, id_ in enumerate(v_ids):
        combined[id_] = combined.get(id_, 0) + 0.5 / (rank + 1)
    for rank, id_ in enumerate(k_ids):
        combined[id_] = combined.get(id_, 0) + 0.5 / (rank + 1)

    sorted_ids = sorted(combined, key=combined.get, reverse=True)

    # Batch-fetch all child metadata in one ChromaDB call (avoids N+1).
    # Build a dict first so we can iterate in RRF order — collection.get()
    # returns results in internal storage order, not in the requested id order.
    seen = set(r["id"] for r in exact_results)
    candidates = list(exact_results)

    if sorted_ids:
        batch = collection.get(ids=sorted_ids, include=["metadatas"])
        id_to_meta = dict(zip(batch["ids"], batch["metadatas"]))
        for id_ in sorted_ids:
            meta = id_to_meta.get(id_)
            if meta is None:
                continue
            parent_id = meta["parent_id"]
            if parent_id not in seen:
                seen.add(parent_id)
                parent = parent_lookup.get(parent_id)
                if parent:
                    candidates.append(parent)
            if len(candidates) >= top_k * 2:
                break

    if not candidates:
        return []

    return candidates[:top_k]


@newrelic.agent.function_trace()
def retrieve(question: str, history: list[HistoryItem] | None = None) -> tuple[list[Chunk], dict[str, Any]]:
    """Full retrieval pipeline: classify, then search appropriately."""
    if history is None:
        history = []
    classification = classify_query(question, history)
    query = classification["rewritten_query"]
    query_type = classification["query_type"]
    object_names = classification.get("object_names", [])

    obs_cm = _start_obs_ctx("retrieve", as_type="span",
                            input=question,
                            metadata={"rewritten_query": query, "query_type": query_type,
                                      "object_names": object_names})

    with obs_cm as obs:
        # Compute query embedding once; reused across all vector_search calls below.
        with newrelic.agent.ExternalTrace('voyageai', 'api.voyageai.com', 'POST'):
            query_vector = vc.embed([query], model="voyage-3-lite").embeddings[0]

        if query_type == "object_reference":
            chunks = hybrid_retrieve(
                query,
                content_type="object_reference",
                object_names=object_names,
                bm25_query=question,
                query_vector=query_vector,
            )
        elif query_type == "conceptual":
            chunks = hybrid_retrieve(
                query,
                content_type="conceptual",
                bm25_query=question,
                query_vector=query_vector,
            )
        else:  # "both"
            obj_results = hybrid_retrieve(
                query,
                top_k=3,
                content_type="object_reference",
                object_names=object_names,
                bm25_query=question,
                query_vector=query_vector,
            )
            conceptual_results = hybrid_retrieve(
                query,
                top_k=3,
                content_type="conceptual",
                bm25_query=question,
                query_vector=query_vector,
            )
            chunks = obj_results + conceptual_results

        obs.update(
            output={"chunk_count": len(chunks),
                    "headings": [c.get("heading_path", "")[:120] for c in chunks[:5]]},
            metadata={"query_type": query_type, "object_names": object_names,
                      "chunk_sources": [c.get("source", "") for c in chunks[:5]]},
        )
    return chunks, classification


def _build_chat_messages(
    question: str,
    context_chunks: list[Chunk],
    history: list[HistoryItem],
) -> tuple[list[dict], list[dict]]:
    """Assemble the system block and messages list for a Claude generation call."""
    context_parts = []
    for chunk in context_chunks:
        source = chunk.get("source", "")
        url = chunk.get("url", "")
        heading = chunk.get("heading_path", "")
        text = chunk.get("text", "")
        if source == "iem_reference":
            context_parts.append(f"[IEM Object Reference: {heading}]\n{text}\nSource: {url}")
        else:
            context_parts.append(f"[MSP Manual: {heading}]\n{text}\nSource: {url}")

    context_str = "\n\n---\n\n".join(context_parts)

    # cache_control marks the stable system prompt for Anthropic prompt caching:
    # identical text on every request → cache hit after the first call, charged
    # at 10% of normal input token cost.
    system_block = [{
        "type": "text",
        "text": SYSTEM_PROMPT,
        "cache_control": {"type": "ephemeral"},
    }]

    messages = list(history) + [{
        "role": "user",
        "content": f"Documentation excerpts:\n\n{context_str}\n\n---\n\nQuestion: {question}"
    }]

    return system_block, messages


def _chunks_to_sources(chunks: list[Chunk]) -> list[dict]:
    return [
        {
            "heading_path": c.get("heading_path", ""),
            "url": c.get("url", ""),
            "source": c.get("source", ""),
            "content_type": c.get("content_type", ""),
            "object_name": c.get("object_name"),
        }
        for c in chunks
    ]


def generate_response(question: str, context_chunks: list[Chunk], history: list[HistoryItem] | None = None) -> str:
    """Generate a response using Claude with retrieved context."""
    if history is None:
        history = []
    system_block, messages = _build_chat_messages(question, context_chunks, history)
    obs_cm = _start_obs_ctx("generate_response", as_type="generation",
                            model="claude-sonnet-4-6",
                            input=question,
                            metadata={"chunk_count": len(context_chunks)})
    with obs_cm as obs:
        response = llm.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=system_block,
            messages=messages
        )
        obs.update(output=response.content[0].text, usage_details=_token_usage(response))
    return response.content[0].text


@newrelic.agent.function_trace()
async def _async_generate_response_stream(
    question: str,
    context_chunks: list[Chunk],
    history: list[HistoryItem],
    _usage_capture: list[Any] | None = None,
):
    """Async generator that yields text chunks from the Claude streaming API.

    If _usage_capture is a list, the final message's usage object is appended
    to it after the stream completes, so callers can read token counts.
    """
    system_block, messages = _build_chat_messages(question, context_chunks, history)
    with newrelic.agent.ExternalTrace('anthropic', 'api.anthropic.com', 'POST'):
        async with async_llm.messages.stream(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=system_block,
            messages=messages,
        ) as stream:
            async for text in stream.text_stream:
                yield text
            final = await stream.get_final_message()
            if _usage_capture is not None:
                _usage_capture.append(final.usage)


async def async_stream_chat(question: str, history: list[HistoryItem] | None = None):
    """Async generator entry point for SSE streaming. Yields event dicts."""
    if history is None:
        history = []

    root = _start_obs("chat", as_type="span", input=question,
                      metadata={"history_turns": len(history) // 2})

    try:
        history = await asyncio.to_thread(_compress_history, history)

        key = _make_cache_key(question)
        with _cache_lock:
            hit = _response_cache.get(key)

        if hit is not None:
            newrelic.agent.record_custom_metric('Custom/Cache/Hit', 1)
            logger.info("cache HIT  [size=%d] q=%r", len(_response_cache), question[:80])
            answer, chunks, classification, extend = hit
            new_history = (
                history + [
                    {"role": "user", "content": question},
                    {"role": "assistant", "content": _strip_patch_blocks(answer)},
                ]
                if extend else history
            )
            root.update(output=answer[:500], metadata={"cache": "hit"})
            yield {"type": "meta", "sources": _chunks_to_sources(chunks), "query_type": classification["query_type"]}
            yield {"type": "chunk", "text": answer}
            yield {"type": "done", "history": new_history[-HISTORY_MAX_OUTGOING:]}
            return

        newrelic.agent.record_custom_metric('Custom/Cache/Miss', 1)
        logger.info("cache MISS [size=%d] q=%r", len(_response_cache), question[:80])
        context_chunks, classification = await asyncio.to_thread(retrieve, question, history)

        if not context_chunks:
            answer = (
                "I couldn't find any relevant Pure Data documentation for that question. "
                "Try asking about a specific Pd object, patching concept, or audio technique."
            )
            with _cache_lock:
                _response_cache[key] = (answer, [], classification, False)
            root.update(output=answer, metadata={"retrieved_chunks": 0, "cache": "miss"})
            yield {"type": "meta", "sources": [], "query_type": classification["query_type"]}
            yield {"type": "chunk", "text": answer}
            yield {"type": "done", "history": history}
            return

        yield {"type": "meta", "sources": _chunks_to_sources(context_chunks), "query_type": classification["query_type"]}

        gen = _start_obs("generate_response", as_type="generation",
                         model="claude-sonnet-4-6",
                         input=question,
                         metadata={"chunk_count": len(context_chunks)})
        full_text = ""
        usage_capture: list[Any] = []
        async for text in _async_generate_response_stream(question, context_chunks, history, _usage_capture=usage_capture):
            full_text += text
            yield {"type": "chunk", "text": text}
        if usage_capture:
            gen.update(output=full_text[:500], usage_details=_token_usage(usage_capture[0]))
        gen.end()

        new_history = history + [
            {"role": "user", "content": question},
            {"role": "assistant", "content": _strip_patch_blocks(full_text)},
        ]
        with _cache_lock:
            _response_cache[key] = (full_text, context_chunks, classification, True)
        root.update(output=full_text[:500], metadata={"retrieved_chunks": len(context_chunks),
                                                      "query_type": classification["query_type"],
                                                      "cache": "miss"})
        yield {"type": "done", "history": new_history[-HISTORY_MAX_OUTGOING:]}

    finally:
        root.end()


def _strip_patch_blocks(text: str) -> str:
    """Remove pd-patch JSON blocks before storing in history.
    The LLM doesn't need to re-read its own patch JSON on follow-up turns,
    and leaving it in would inflate context tokens and risk length validation errors."""
    # Strip complete blocks first (opening + closing fence)
    text = re.sub(r'```pd-patch\b.*?```', '[patch diagram]', text, flags=re.DOTALL)
    # Strip truncated blocks — no closing fence, runs to end of string
    text = re.sub(r'```pd-patch\b.*', '[patch diagram]', text, flags=re.DOTALL)
    return text.strip()


def chat(question: str, history: list[HistoryItem] | None = None) -> tuple[str, list[Chunk], dict, list[HistoryItem]]:
    """Single-turn entry point used by the API. Returns (answer, chunks, classification, new_history)."""
    if history is None:
        history = []

    with _start_obs_ctx("chat", as_type="span", input=question,
                        metadata={"history_turns": len(history) // 2}) as root:
        history = _compress_history(history)

        key = _make_cache_key(question)
        with _cache_lock:
            hit = _response_cache.get(key)

        if hit is not None:
            logger.info("cache HIT  [size=%d] q=%r", len(_response_cache), question[:80])
            answer, chunks, classification, extend = hit
            new_history = (
                history + [
                    {"role": "user", "content": question},
                    {"role": "assistant", "content": _strip_patch_blocks(answer)},
                ]
                if extend else history
            )
            root.update(output=answer[:500], metadata={"cache": "hit"})
            return answer, chunks, classification, new_history

        logger.info("cache MISS [size=%d] q=%r", len(_response_cache), question[:80])
        context_chunks, classification = retrieve(question, history)
        if not context_chunks:
            answer = (
                "I couldn't find any relevant Pure Data documentation for that question. "
                "Try asking about a specific Pd object, patching concept, or audio technique."
            )
            with _cache_lock:
                _response_cache[key] = (answer, [], classification, False)
            root.update(output=answer, metadata={"retrieved_chunks": 0, "cache": "miss"})
            return answer, [], classification, history

        answer = generate_response(question, context_chunks, history)
        new_history = history + [
            {"role": "user", "content": question},
            {"role": "assistant", "content": _strip_patch_blocks(answer)},
        ]
        with _cache_lock:
            _response_cache[key] = (answer, context_chunks, classification, True)
        root.update(output=answer[:500], metadata={"retrieved_chunks": len(context_chunks),
                                                    "query_type": classification["query_type"],
                                                    "cache": "miss"})
        return answer, context_chunks, classification, new_history


def main():
    """Interactive chat loop for the Pd documentation assistant."""
    print("Pure Data Documentation Assistant")
    print("Ask questions about Pure Data objects, patching, and concepts.")
    print("Type 'quit' or 'exit' to stop.\n")

    history = []

    while True:
        try:
            question = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not question:
            continue
        if question.lower() in ("quit", "exit"):
            print("Goodbye!")
            break

        context_chunks, classification = retrieve(question, history)

        if not context_chunks:
            print("Assistant: I couldn't find relevant documentation for that question.\n")
            continue

        answer = generate_response(question, context_chunks, history)
        print(f"\nAssistant: {answer}\n")

        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": answer})
        # Keep last 10 turns to prevent context overflow
        if len(history) > 20:
            history = history[-20:]


if __name__ == "__main__":
    main()
