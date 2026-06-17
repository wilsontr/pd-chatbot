# rag.py
import asyncio
import hashlib
import json
import logging
import threading
import newrelic.agent
import chromadb
import cachetools
from dotenv import load_dotenv
load_dotenv()
import voyageai
from rank_bm25 import BM25Okapi
import anthropic

logger = logging.getLogger(__name__)

_response_cache: cachetools.TTLCache = cachetools.TTLCache(maxsize=256, ttl=3600)
_cache_lock = threading.Lock()

def _make_cache_key(question: str) -> str:
    # Key on the normalized question only, not history. Documentation answers are
    # objective — the same question asked mid-session should return the cached answer
    # rather than paying full retrieval + LLM cost again.
    return hashlib.sha256(question.strip().lower().encode()).hexdigest()

vc = voyageai.Client()
db = chromadb.PersistentClient(path="./chroma_db")
collection = db.get_collection("pd_docs")
llm = anthropic.Anthropic()
async_llm = anthropic.AsyncAnthropic()

with open("child_chunks.json") as f:
    child_chunks = json.load(f)
with open("parent_lookup.json") as f:
    parent_lookup = json.load(f)

# Build BM25 index over all child chunks for keyword search
tokenized_corpus = [c["text"].lower().split() for c in child_chunks]
bm25 = BM25Okapi(tokenized_corpus)


@newrelic.agent.function_trace()
def classify_query(question, history):
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

    with newrelic.agent.ExternalTrace('anthropic', 'api.anthropic.com', 'POST'):
        response = llm.messages.create(
            model="claude-haiku-4-5",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}]
        )
    try:
        return json.loads(response.content[0].text.strip())
    except json.JSONDecodeError:
        return {
            "rewritten_query": question,
            "query_type": "both",
            "object_names": []
        }


def vector_search(query, top_k=10, content_type=None, object_name=None):
    """Search ChromaDB with optional metadata filters."""
    with newrelic.agent.ExternalTrace('voyageai', 'api.voyageai.com', 'POST'):
        query_vector = vc.embed([query], model="voyage-3-lite").embeddings[0]

    where = {}
    if content_type:
        where["content_type"] = {"$eq": content_type}
    if object_name:
        where["object_name"] = {"$eq": object_name}

    results = collection.query(
        query_embeddings=[query_vector],
        n_results=top_k,
        where=where if where else None,
        include=["metadatas", "distances"]
    )
    return results["ids"][0], results["metadatas"][0]


def bm25_search(query, top_k=10, content_type=None):
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


def hybrid_retrieve(query, top_k=5, content_type=None, object_names=None, bm25_query=None):
    """
    Combine vector search and BM25 via RRF, return top-k by rank.
    If specific object names are mentioned, also do exact lookups.
    bm25_query defaults to query but can be set to the original user question
    to preserve exact object name tokens (e.g. osc~) that a rewritten query may drop.
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
                object_name=name
            )
            for id_, meta in zip(exact_ids, exact_metas):
                parent = parent_lookup.get(meta["parent_id"])
                if parent:
                    exact_results.append(parent)

    # Broad vector + BM25 search
    v_ids, v_metas = vector_search(query, top_k=top_k * 2, content_type=content_type)
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
def retrieve(question, history=None):
    """Full retrieval pipeline: classify, then search appropriately."""
    if history is None:
        history = []
    classification = classify_query(question, history)
    query = classification["rewritten_query"]
    query_type = classification["query_type"]
    object_names = classification.get("object_names", [])

    if query_type == "object_reference":
        return hybrid_retrieve(
            query,
            content_type="object_reference",
            object_names=object_names,
            bm25_query=question
        ), classification

    elif query_type == "conceptual":
        return hybrid_retrieve(
            query,
            content_type="conceptual",
            bm25_query=question
        ), classification

    else:  # "both"
        obj_results = hybrid_retrieve(
            query,
            top_k=3,
            content_type="object_reference",
            object_names=object_names,
            bm25_query=question
        )
        conceptual_results = hybrid_retrieve(
            query,
            top_k=3,
            content_type="conceptual",
            bm25_query=question
        )
        return obj_results + conceptual_results, classification


def _build_chat_messages(question, context_chunks, history):
    """Build system prompt and messages list for the generation LLM call."""
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

    system_prompt = (
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

    messages = list(history) + [{
        "role": "user",
        "content": f"Documentation excerpts:\n\n{context_str}\n\n---\n\nQuestion: {question}"
    }]

    return system_prompt, messages


def _chunks_to_sources(chunks):
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


def generate_response(question, context_chunks, history=None):
    """Generate a response using Claude with retrieved context."""
    if history is None:
        history = []
    system_prompt, messages = _build_chat_messages(question, context_chunks, history)
    response = llm.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=system_prompt,
        messages=messages
    )
    return response.content[0].text


@newrelic.agent.function_trace()
async def _async_generate_response_stream(question, context_chunks, history):
    """Async generator that yields text chunks from the Claude streaming API."""
    system_prompt, messages = _build_chat_messages(question, context_chunks, history)
    with newrelic.agent.ExternalTrace('anthropic', 'api.anthropic.com', 'POST'):
        async with async_llm.messages.stream(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=system_prompt,
            messages=messages,
        ) as stream:
            async for text in stream.text_stream:
                yield text


async def async_stream_chat(question, history=None):
    """Async generator entry point for SSE streaming. Yields event dicts."""
    if history is None:
        history = []

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
        yield {"type": "meta", "sources": _chunks_to_sources(chunks), "query_type": classification["query_type"]}
        yield {"type": "chunk", "text": answer}
        yield {"type": "done", "history": new_history}
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
        yield {"type": "meta", "sources": [], "query_type": classification["query_type"]}
        yield {"type": "chunk", "text": answer}
        yield {"type": "done", "history": history}
        return

    yield {"type": "meta", "sources": _chunks_to_sources(context_chunks), "query_type": classification["query_type"]}

    full_text = ""
    async for text in _async_generate_response_stream(question, context_chunks, history):
        full_text += text
        yield {"type": "chunk", "text": text}

    new_history = history + [
        {"role": "user", "content": question},
        {"role": "assistant", "content": _strip_patch_blocks(full_text)},
    ]
    with _cache_lock:
        _response_cache[key] = (full_text, context_chunks, classification, True)
    yield {"type": "done", "history": new_history}


def _strip_patch_blocks(text: str) -> str:
    """Remove pd-patch JSON blocks before storing in history.
    The LLM doesn't need to re-read its own patch JSON on follow-up turns,
    and leaving it in would inflate context tokens and risk length validation errors."""
    import re
    # Strip complete blocks first (opening + closing fence)
    text = re.sub(r'```pd-patch\b.*?```', '[patch diagram]', text, flags=re.DOTALL)
    # Strip truncated blocks — no closing fence, runs to end of string
    text = re.sub(r'```pd-patch\b.*', '[patch diagram]', text, flags=re.DOTALL)
    return text.strip()


def chat(question, history=None):
    """Single-turn entry point used by the API. Returns (answer, chunks, classification, new_history)."""
    if history is None:
        history = []

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
        return answer, [], classification, history

    answer = generate_response(question, context_chunks, history)
    new_history = history + [
        {"role": "user", "content": question},
        {"role": "assistant", "content": _strip_patch_blocks(answer)},
    ]
    with _cache_lock:
        _response_cache[key] = (answer, context_chunks, classification, True)
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
