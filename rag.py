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
from pd_schema import PdPatch
from model_config import ANSWER_MODEL, JUDGE_MODEL, max_tokens_for, thinking_kwargs
import cost_tracker
import pricing
import scope
from llm_response import text_of
from evaluation.judge import score_faithfulness
from evaluation.retry import with_retries

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


class _SimpleUsage:
    """Minimal ``.usage`` stand-in so _cost_details can price a token dict."""

    def __init__(self, tokens: dict[str, int]):
        self.usage = type("Usage", (), {
            "input_tokens": tokens.get("input_tokens", 0),
            "output_tokens": tokens.get("output_tokens", 0),
            "cache_read_input_tokens": tokens.get("cache_read_tokens", 0),
            "cache_creation_input_tokens": tokens.get("cache_write_tokens", 0),
        })()


def _cost_details(model: str, resp: Any) -> dict[str, float] | None:
    """Langfuse per-observation cost, from the same pricing table the eval uses."""
    usage = _token_usage(resp)
    if not usage:
        return None
    cost = pricing.cost_usd(model, usage)
    return {"total": round(cost, 8)} if cost is not None else None


def _record_scope_decision(scope_label: str, *, refused: bool) -> None:
    """Count refusals in New Relic and score the decision in Langfuse."""
    if refused:
        newrelic.agent.record_custom_metric("Custom/Guard/OutOfScope", 1)
        logger.info("scope refusal (%s)", scope_label)
    if _langfuse is not None:
        try:
            _langfuse.score_current_span(name="scope", value=0 if refused else 1)
        except Exception:
            logger.debug("failed to score scope", exc_info=True)


def _record_request_cost(root: Any, meta: dict[str, Any], summary: dict[str, Any]) -> None:
    """Attach the request's cost to its trace and to New Relic."""
    meta.update({
        "cost_usd": summary["total_usd"],
        "cache_savings_usd": summary["cache_savings_usd"],
        "llm_calls": summary["calls"],
        "cost_by_kind": summary["by_kind"],
    })
    if summary["unpriced_models"]:
        meta["unpriced_models"] = summary["unpriced_models"]
    try:
        root.update(metadata=meta, cost_details={"total": summary["total_usd"]})
    except Exception:
        logger.debug("failed to attach cost to trace", exc_info=True)
    newrelic.agent.record_custom_metric("Custom/Cost/RequestUsd", summary["total_usd"])
    newrelic.agent.record_custom_metric(
        "Custom/Cost/CacheSavingsUsd", summary["cache_savings_usd"]
    )
    logger.info(
        "request cost $%.6f (cache savings $%.6f) across %d model call(s)",
        summary["total_usd"], summary["cache_savings_usd"], summary["calls"],
    )


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
    "Stay in scope: only help with Pure Data, its objects, patching, audio/MIDI technique "
    "and the official documentation. If a request is unrelated to Pd, say briefly that you "
    "only cover Pd and suggest a Pd topic instead of answering it. "
    "Refuse requests for harmful, illegal or disallowed content, including requests framed as "
    "Pd tasks (for example patches designed to injure people, damage equipment or disrupt "
    "other systems): decline plainly and do not offer a workaround. "
    "Use the provided documentation excerpts as your primary source, and treat them as "
    "authoritative over anything else. "
    "Never state a specific numeric argument, default value, inlet/outlet count, or object "
    "behavior unless it is explicitly present in the provided excerpts — if you are not sure "
    "an excerpt covers it, say so instead of guessing. "
    "You may draw on broader knowledge of Pd to explain general concepts or suggest "
    "implementation strategies, but you must explicitly flag when you do so (e.g. "
    "\"this isn't covered in the docs, but...\") rather than blending it in as if it were "
    "documented. "
    "If the documentation contradicts general knowledge, prefer the documentation. "
    "If the excerpts don't contain enough information to answer confidently, say so plainly "
    "instead of filling the gap with a plausible-sounding guess. "
    "Include relevant object names and brief examples where helpful. "
    "Citation rules (these override anything else about sources):\n"
    "- Every URL you cite must be copied character-for-character from a line starting with "
    "\"Source:\" in the excerpts above. Never invent, shorten, guess, normalise or reconstruct a URL.\n"
    "- Never cite a URL that does not appear in the excerpts, even if you believe it exists. Do not "
    "construct pd.iem.sh links, manual page paths, or section anchors from memory.\n"
    "- End every answer with a \"Sources:\" section: one bullet per source you actually used, each "
    "bullet copied exactly as it appears after \"Source:\". If the excerpts contain no URL, write "
    "\"Sources: none in the provided excerpts\" instead of guessing.\n"
    "- Attribute each specific claim to the excerpt heading that supports it, and never attribute a "
    "claim to a source that does not support it.\n\n"
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


# Scope rules travel in the classifier's system block so the user-message routing
# instructions stay close to their original form (see classify_query).
CLASSIFIER_SYSTEM_PROMPT = (
    "You classify questions for a Pure Data documentation assistant.\n\n"
    + scope.SCOPE_CLASSIFICATION_RULES
)


_OBJECT_IN_BRACKETS = re.compile(r"\[([A-Za-z0-9_~.+\-]{1,40})(?:\s[^\]]*)?\]")


def object_names_from_question(question: str) -> list[str]:
    """Object names written as [osc~] in the question itself.

    Deterministic companion to the classifier's object_names: the exact-object
    lookup is the part of hybrid retrieval that beats vector-only, and relying on
    an LLM to re-extract names the user already wrote in brackets made recall
    swing between runs.
    """
    names: list[str] = []
    for match in _OBJECT_IN_BRACKETS.finditer(question):
        name = match.group(1).strip()
        if name and name not in names:
            names.append(name)
    return names


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
            resp = with_retries(
                llm.messages.create,
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
        gen.update(output=text_of(resp), usage_details=_token_usage(resp))
        if _token_usage(resp):
            gen.update(cost_details=_cost_details("claude-haiku-4-5", resp))
        cost_tracker.record("history_compression", "claude-haiku-4-5", _token_usage(resp))
    summary = text_of(resp).strip()
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

    # Scope rules go in the system block so the routing instructions below stay
    # almost word-for-word what they were before the gate existed: appending scope
    # rules to the same message measurably degraded retrieval (hybrid recall@5
    # 0.78 -> 0.75) by changing how the model rewrote queries.
    prompt = f"""You are helping route questions about Pure Data (Pd) to the right documentation source.

{"Conversation history:\n" + history_text if history_text else ""}

Question: {question}

Respond in JSON with these fields:
- "rewritten_query": standalone version of the question incorporating any context from history
- "query_type": one of "object_reference", "conceptual", or "both". Use "object_reference" when the question is primarily about one or more named Pd objects (for example "What are the inlets of [osc~]?"); use "conceptual" for patching or technique questions that name no object; use "both" only when the question genuinely needs both kinds of documentation.
- "object_names": list of Pd object names mentioned (e.g. ["osc~", "pack"]), empty list if none
- "scope": one of "in_scope", "off_topic", "mixed", "policy_violation"
- "off_topic_part": the non-Pd part when scope is "mixed", otherwise ""
- "pd_query": when scope is "mixed", the Pure Data part of the question as a standalone search query; otherwise the same as "rewritten_query"

Return only the JSON object, no other text."""

    obs_cm = _start_obs_ctx("classify_query", as_type="generation",
                            model="claude-haiku-4-5", input=question)
    with obs_cm as obs:
        with newrelic.agent.ExternalTrace('anthropic', 'api.anthropic.com', 'POST'):
            response = with_retries(
                llm.messages.create,
                model="claude-haiku-4-5",
                # Six fields plus scope reasoning; 200 tokens truncated the JSON
                # often enough to fall back to the default classification, which
                # measurably hurt hybrid retrieval (recall@5 0.78 -> 0.72).
                max_tokens=400,
                system=[{"type": "text", "text": CLASSIFIER_SYSTEM_PROMPT}],
                messages=[{"role": "user", "content": prompt}]
            )
        raw = text_of(response).strip()
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
                "object_names": [],
                "scope": scope.IN_SCOPE,
                "off_topic_part": "",
                "pd_query": question,
            }
        result["scope"] = scope.classify_scope(result.get("scope"))
        result.setdefault("off_topic_part", "")
        result.setdefault("pd_query", result.get("rewritten_query", question))
        obs.update(output=result, usage_details=_token_usage(response))
        if _token_usage(response):
            obs.update(cost_details=_cost_details("claude-haiku-4-5", response))
        cost_tracker.record("classify", "claude-haiku-4-5", _token_usage(response))
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


def max_similarity(
    query: str,
    *,
    query_vector: list[float] | None = None,
    content_type: str | None = None,
    n_results: int = 5,
) -> float | None:
    """Best cosine similarity among the top matches, or None when nothing matches.

    Chroma is configured with ``hnsw:space=cosine``, so it returns ``1 - cosine
    similarity`` as the distance. This is what makes a relevance cutoff possible:
    the hybrid retriever ranks by RRF and throws the distances away, so a query
    with no real match still comes back with its five least-bad chunks.
    """
    if query_vector is None:
        with newrelic.agent.ExternalTrace('voyageai', 'api.voyageai.com', 'POST'):
            query_vector = vc.embed([query], model="voyage-3-lite").embeddings[0]
    where = {"content_type": {"$eq": content_type}} if content_type else None
    results = collection.query(
        query_embeddings=[query_vector],
        n_results=n_results,
        where=where,
        include=["distances"],
    )
    distances = (results.get("distances") or [[]])[0]
    if not distances:
        return None
    return 1.0 - min(distances)


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
def retrieve(
    question: str,
    history: list[HistoryItem] | None = None,
    *,
    classification: dict[str, Any] | None = None,
) -> tuple[list[Chunk], dict[str, Any]]:
    """Full retrieval pipeline: classify, then search appropriately.

    ``classification`` lets a caller that already classified the question (the
    scope gate does) avoid a second model call.

    Returns no chunks — which routes the caller to its "no relevant documentation"
    message — when the question is out of scope or when the best match is below
    ``scope.MIN_RELEVANCE_SIMILARITY``.
    """
    if history is None:
        history = []
    if classification is None:
        classification = classify_query(question, history)
    if scope.is_refusal(classification.get("scope")):
        return [], classification
    query_type = classification["query_type"]
    object_names = list(classification.get("object_names", []))
    written_objects = object_names_from_question(question)
    for name in written_objects:
        if name not in object_names:
            object_names.append(name)

    # A mixed question's combined query embeds poorly against Pd documentation
    # (measured 0.41 against a 0.42 cutoff for "[osc~]..., and the capital of
    # France?"), which would drop the Pd part. The classifier supplies a Pd-only
    # query for that case, used for both search and the relevance cutoff.
    mixed = scope.decide(classification.get("scope")) == scope.ANSWER_WITH_DECLINE
    if mixed:
        query = classification.get("pd_query") or classification["rewritten_query"]
    elif history:
        # The rewrite exists to resolve references in follow-ups; with no history
        # it only adds classifier noise to the embedding.
        query = classification["rewritten_query"]
    else:
        query = question
    bm25_query = query if mixed else question

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
                bm25_query=bm25_query,
                query_vector=query_vector,
            )
        elif query_type == "conceptual":
            chunks = hybrid_retrieve(
                query,
                content_type="conceptual",
                bm25_query=bm25_query,
                query_vector=query_vector,
            )
        else:  # "both"
            obj_results = hybrid_retrieve(
                query,
                top_k=3,
                content_type="object_reference",
                object_names=object_names,
                bm25_query=bm25_query,
                query_vector=query_vector,
            )
            conceptual_results = hybrid_retrieve(
                query,
                top_k=3,
                content_type="conceptual",
                bm25_query=bm25_query,
                query_vector=query_vector,
            )
            # Interleave the two sources instead of concatenating them: recall@5
            # scores the first five chunks, and putting all three object results
            # first buried the conceptual match a mixed question needed.
            chunks = []
            for obj, con in zip(obj_results, conceptual_results):
                chunks.extend([obj, con])
            if len(obj_results) > len(conceptual_results):
                chunks.extend(obj_results[len(conceptual_results):])
            elif len(conceptual_results) > len(obj_results):
                chunks.extend(conceptual_results[len(obj_results):])

        # Relevance backstop: hybrid retrieval always returns its top-k, so
        # without this a question with no real match would still be answered
        # from five irrelevant chunks.
        best_similarity = max_similarity(
            query, query_vector=query_vector, content_type=None
        ) if chunks else None
        below_cutoff = (
            best_similarity is not None
            and best_similarity < scope.MIN_RELEVANCE_SIMILARITY
        )
        if below_cutoff:
            logger.info(
                "low relevance (best similarity %.3f < %.3f) q=%r",
                best_similarity, scope.MIN_RELEVANCE_SIMILARITY, question[:80],
            )
            newrelic.agent.record_custom_metric("Custom/Guard/LowRelevance", 1)
            chunks = []

        obs.update(
            output={"chunk_count": len(chunks),
                    "headings": [c.get("heading_path", "")[:120] for c in chunks[:5]]},
            metadata={"query_type": query_type, "object_names": object_names,
                      "chunk_sources": [c.get("source", "") for c in chunks[:5]],
                      "best_similarity": round(best_similarity, 3) if best_similarity is not None else None,
                      "below_relevance_cutoff": below_cutoff},
        )
    return chunks, classification


def _build_chat_messages(
    question: str,
    context_chunks: list[Chunk],
    history: list[HistoryItem],
    system_prompt: str | None = None,
    scope_note: str | None = None,
) -> tuple[list[dict], list[dict]]:
    """Assemble the system block and messages list for a Claude generation call.

    ``system_prompt`` lets the evaluation harness score an alternative prompt
    against the same retrieval and context without editing production code; it
    defaults to the shipped SYSTEM_PROMPT.
    """
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
        "text": system_prompt or SYSTEM_PROMPT,
        "cache_control": {"type": "ephemeral"},
    }]

    user_content = f"Documentation excerpts:\n\n{context_str}\n\n---\n\nQuestion: {question}"
    if scope_note:
        user_content += f"\n\n{scope_note}"
    messages = list(history) + [{"role": "user", "content": user_content}]

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


def generate_response(
    question: str,
    context_chunks: list[Chunk],
    history: list[HistoryItem] | None = None,
    scope_note: str | None = None,
) -> str:
    """Generate a response using Claude with retrieved context."""
    if history is None:
        history = []
    system_block, messages = _build_chat_messages(
        question, context_chunks, history, scope_note=scope_note
    )
    obs_cm = _start_obs_ctx("generate_response", as_type="generation",
                            model=ANSWER_MODEL,
                            input=question,
                            metadata={"chunk_count": len(context_chunks)})
    with obs_cm as obs:
        response = with_retries(
            llm.messages.create,
            model=ANSWER_MODEL,
            max_tokens=max_tokens_for(ANSWER_MODEL),
            system=system_block,
            messages=messages,
            **thinking_kwargs(ANSWER_MODEL),
        )
        obs.update(output=text_of(response), usage_details=_token_usage(response))
        if _token_usage(response):
            obs.update(cost_details=_cost_details(ANSWER_MODEL, response))
        cost_tracker.record("generate", ANSWER_MODEL, _token_usage(response))
    return text_of(response)


@newrelic.agent.function_trace()
async def _async_generate_response_stream(
    question: str,
    context_chunks: list[Chunk],
    history: list[HistoryItem],
    _usage_capture: list[Any] | None = None,
    scope_note: str | None = None,
):
    """Async generator that yields text chunks from the Claude streaming API.

    If _usage_capture is a list, the final message's usage object is appended
    to it after the stream completes, so callers can read token counts.
    """
    system_block, messages = _build_chat_messages(
        question, context_chunks, history, scope_note=scope_note
    )
    with newrelic.agent.ExternalTrace('anthropic', 'api.anthropic.com', 'POST'):
        async with async_llm.messages.stream(
            model=ANSWER_MODEL,
            max_tokens=max_tokens_for(ANSWER_MODEL),
            system=system_block,
            messages=messages,
            **thinking_kwargs(ANSWER_MODEL),
        ) as stream:
            async for text in stream.text_stream:
                yield text
            final = await stream.get_final_message()
            cost_tracker.record("generate", ANSWER_MODEL, _token_usage(final))
            if _usage_capture is not None:
                _usage_capture.append(final)


async def async_stream_chat(question: str, history: list[HistoryItem] | None = None):
    """Async generator entry point for SSE streaming. Yields event dicts."""
    if history is None:
        history = []

    if _check_prompt_injection(question):
        newrelic.agent.record_custom_metric("Custom/Guard/InjectionBlocked", 1)
        logger.warning("prompt injection blocked: %r", question[:120])
        blocked = "I can only answer questions about Pure Data documentation. Please ask a question about Pd objects, patching concepts, or audio techniques."
        yield {"type": "meta", "sources": [], "query_type": "conceptual"}
        yield {"type": "chunk", "text": blocked}
        yield {"type": "done", "history": history}
        return

    root_meta: dict[str, Any] = {"history_turns": len(history) // 2}
    root = _start_obs("chat", as_type="span", input=question, metadata=root_meta)
    cost_token = cost_tracker.start()

    try:
        history = await asyncio.to_thread(_compress_history, history)

        key = _make_cache_key(question)
        with _cache_lock:
            hit = _response_cache.get(key)

        if hit is not None:
            newrelic.agent.record_custom_metric('Custom/Cache/Hit', 1)
            logger.info("cache HIT  [size=%d] q=%r", len(_response_cache), question[:80])
            _, answer, chunks, classification, extend = hit
            new_history = (
                history + [
                    {"role": "user", "content": question},
                    {"role": "assistant", "content": _strip_patch_blocks(answer)},
                ]
                if extend else history
            )
            root_meta["cache"] = "hit"
            root.update(output=answer[:500], metadata=root_meta)
            yield {"type": "meta", "sources": _chunks_to_sources(chunks), "query_type": classification["query_type"]}
            yield {"type": "chunk", "text": answer}
            yield {"type": "done", "history": new_history[-HISTORY_MAX_OUTGOING:]}
            return

        newrelic.agent.record_custom_metric('Custom/Cache/Miss', 1)
        logger.info("cache MISS [size=%d] q=%r", len(_response_cache), question[:80])
        classification = await asyncio.to_thread(classify_query, question, history)
        scope_action = scope.decide(classification.get("scope"))
        if scope_action in (scope.REFUSE_OFF_TOPIC, scope.REFUSE_POLICY):
            answer = scope.refusal_text(classification.get("scope"))
            _record_scope_decision(classification.get("scope"), refused=True)
            with _cache_lock:
                _response_cache[key] = (question, answer, [], classification, False)
            root_meta.update({
                "cache": "miss",
                "scope": classification.get("scope"),
                "refused": True,
            })
            root.update(output=answer, metadata=root_meta)
            yield {"type": "meta", "sources": [],
                   "query_type": classification.get("query_type", "both")}
            yield {"type": "chunk", "text": answer}
            yield {"type": "done", "history": history}
            return
        _record_scope_decision(classification.get("scope"), refused=False)
        context_chunks, classification = await asyncio.to_thread(
            retrieve, question, history, classification=classification
        )

        if not context_chunks:
            answer = (
                "I couldn't find any relevant Pure Data documentation for that question. "
                "Try asking about a specific Pd object, patching concept, or audio technique."
            )
            with _cache_lock:
                _response_cache[key] = (question, answer, [], classification, False)
            root_meta.update({"retrieved_chunks": 0, "cache": "miss"})
            root.update(output=answer, metadata=root_meta)
            yield {"type": "meta", "sources": [], "query_type": classification["query_type"]}
            yield {"type": "chunk", "text": answer}
            yield {"type": "done", "history": history}
            return

        yield {"type": "meta", "sources": _chunks_to_sources(context_chunks), "query_type": classification["query_type"]}

        gen = _start_obs("generate_response", as_type="generation",
                         model=ANSWER_MODEL,
                         input=question,
                         metadata={"chunk_count": len(context_chunks)})
        full_text = ""
        usage_capture: list[Any] = []
        scope_note = (
            scope.mixed_scope_note(classification)
            if scope.decide(classification.get("scope")) == scope.ANSWER_WITH_DECLINE
            else None
        )
        async for text in _async_generate_response_stream(
            question, context_chunks, history,
            _usage_capture=usage_capture, scope_note=scope_note,
        ):
            full_text += text
            yield {"type": "chunk", "text": text}
        if usage_capture:
            gen.update(output=full_text[:500], usage_details=_token_usage(usage_capture[0]))
        gen.end()

        full_text, patch_failed = _validate_and_canonicalize_blocks(full_text)
        if patch_failed:
            if _langfuse is not None:
                try:
                    _langfuse.score_current_span(name="pd_patch_valid", value=0)
                except Exception:
                    pass

        # Faithfulness can't gate a retry here — the answer is already streamed to the
        # client — so on a low score we surface a visible disclaimer instead of just
        # logging, and store the disclaimer in history/cache so it stays accurate on replay.
        faithfulness = await asyncio.to_thread(_check_faithfulness, question, full_text, context_chunks)
        if faithfulness is not None and (faithfulness.get("score") or 0) <= FAITHFULNESS_RETRY_THRESHOLD:
            newrelic.agent.record_custom_metric("Custom/Guard/LowFaithfulness", 1)
            logger.info("low faithfulness (streaming, score=%s): %s",
                        faithfulness.get("score"), faithfulness.get("explanation"))
            if _langfuse is not None:
                try:
                    _langfuse.score_current_span(name="faithfulness", value=faithfulness.get("score"))
                except Exception:
                    pass
            disclaimer = ("\n\n_Note: parts of this answer may not be fully supported by the "
                          "documentation excerpts — verify against the source docs above._")
            full_text += disclaimer
            yield {"type": "chunk", "text": disclaimer}

        new_history = history + [
            {"role": "user", "content": question},
            {"role": "assistant", "content": _strip_patch_blocks(full_text)},
        ]
        with _cache_lock:
            _response_cache[key] = (question, full_text, context_chunks, classification, True)
        root_meta.update({"retrieved_chunks": len(context_chunks),
                          "query_type": classification["query_type"],
                          "cache": "miss"})
        root.update(output=full_text[:500], metadata=root_meta)
        yield {"type": "done", "history": new_history[-HISTORY_MAX_OUTGOING:]}

    finally:
        cost_summary = cost_tracker.finish(cost_token)
        if cost_summary is not None:
            _record_request_cost(root, root_meta, cost_summary)
        root.end()


# --- Guardrails --------------------------------------------------------------------
# Lightweight prompt-injection detection and groundedness gating.
# These are basic heuristics, not a security boundary — the system prompt already
# constrains the model to prefer documentation. These checks detect the most
# common attack patterns and flag ungrounded responses for observability.

_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above|the)\s+(instructions?|prompts?|rules?|directives?)", re.IGNORECASE),
    re.compile(r"you\s+are\s+(now\s+|no\s+longer\s+)?(a\s+|an\s+)?(?!.*?\bhelpful\b).{0,20}(assistant|chatbot|helper|ai)", re.IGNORECASE),
    re.compile(r"(override|disregard|forget)\s+(your\s+)?(instructions?|prompts?|rules?|system\s+prompt|guidelines?)", re.IGNORECASE),
    re.compile(r"system\s+(prompt|message|instruction)", re.IGNORECASE),
    re.compile(r"(DAN|jailbreak|roleplay|developer\s*mode)", re.IGNORECASE),
    re.compile(r"from\s+now\s+on\s+you\s+(are|will|must|should)", re.IGNORECASE),
    re.compile(r"(do\s+not|don't|never)\s+(follow|obey|use|reference)\s+(your\s+)?(instructions?|prompts?|rules?)", re.IGNORECASE),
]

def _check_prompt_injection(question: str) -> bool:
    """Return True if the question contains known prompt-injection patterns."""
    return any(p.search(question) for p in _INJECTION_PATTERNS)


# Faithfulness score (1-5, LLM-as-judge) at or below this is treated as a real hallucination
# risk rather than noise — see evaluation/judge.py's rubric ("major/near-total unsupported claims").
FAITHFULNESS_RETRY_THRESHOLD = 2

_FAITHFULNESS_RETRY_PROMPT = """Your previous answer contained claims that a faithfulness \
check flagged as not supported by the provided documentation excerpts \
(reason: {explanation}).

Revise your answer:
- Remove or qualify any claim not directly supported by the excerpts.
- If you're not sure something is covered by the docs, say so explicitly \
("not covered in the documentation, but...") instead of stating it as fact.
- Keep the same formatting rules (pd-patch blocks, code spans, tables) as before.

The documentation excerpts and question are unchanged from before this correction."""


def _check_faithfulness(question: str, answer: str, chunks: list[Chunk]) -> dict[str, Any] | None:
    """Run the LLM-as-judge faithfulness check inline (single Haiku call).

    Returns the judge's {"score": int, "explanation": str} dict, or None if there was
    nothing to check (no retrieved chunks) or the judge call itself failed — callers
    should treat None as "skip", not as a failure.
    """
    if not chunks:
        return None
    usage: dict[str, dict[str, int]] = {}
    obs = _start_obs("faithfulness_guard", as_type="generation", model=JUDGE_MODEL)
    try:
        result = score_faithfulness(
            llm, question=question, answer=answer, context_chunks=chunks,
            usage_sink=usage,
        )
        for model, tokens in usage.items():
            cost_tracker.record("faithfulness_guard", model, tokens)
            obs.update(
                usage_details={
                    "input": tokens.get("input_tokens", 0),
                    "output": tokens.get("output_tokens", 0),
                    "cache_read": tokens.get("cache_read_tokens", 0),
                },
                cost_details=_cost_details(model, _SimpleUsage(tokens)),
            )
        return result
    except Exception:
        logger.warning("faithfulness judge call failed", exc_info=True)
    finally:
        obs.end()


""" ---------------------------------------------------------------------- """


def _strip_patch_blocks(text: str) -> str:
    """Remove pd-patch JSON blocks before storing in history.
    The LLM doesn't need to re-read its own patch JSON on follow-up turns,
    and leaving it in would inflate context tokens and risk length validation errors."""
    # Strip complete blocks first (opening + closing fence)
    text = re.sub(r'```pd-patch\b.*?```', '[patch diagram]', text, flags=re.DOTALL)
    # Strip truncated blocks — no closing fence, runs to end of string
    text = re.sub(r'```pd-patch\b.*', '[patch diagram]', text, flags=re.DOTALL)
    return text.strip()


# --- pd-patch structured validation -------------------------------------------------
# The LLM emits patch diagrams as fenced ```pd-patch JSON blocks inside its prose.
# These helpers extract, validate (Pydantic), and canonicalize those blocks, replacing
# malformed ones with a placeholder. The sync chat() path retries on failure.

_PATCH_FENCE_RE = re.compile(r'```pd-patch\b\n?(.*?)```', re.DOTALL)

_RETRY_PROMPT = """Your previous response contained a pd-patch code block with invalid JSON.

Please fix the pd-patch JSON block to match this exact schema:
```json
{
  "objects": [{"id": "str", "type": "obj|msg|floatatom|comment", "text": "str", "inlets": int, "outlets": int}],
  "connections": [{"srcId": "str", "srcOutlet": int, "dstId": "str", "dstInlet": int}]
}
```

Rules:
- objects array: 1-15 items, each with id, type (one of obj/msg/floatatom/comment), text, inlets (>=0), outlets (>=0)
- connections array: unlimited, each with srcId, srcOutlet (>=0), dstId, dstInlet (>=0)
- All id references in connections must match object ids

Do not change the rest of your answer. Only fix the pd-patch JSON block."""


def _extract_pd_patches(text: str) -> list[tuple[str, str]]:
    """Return list of (raw_json, full_fenced_block) for each pd-patch block."""
    return [(m.group(1).strip(), m.group(0)) for m in _PATCH_FENCE_RE.finditer(text)]


def _validate_patch(raw_json: str) -> PdPatch | None:
    """Parse and validate a pd-patch JSON block. Returns PdPatch or None."""
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as e:
        logger.warning("pd-patch JSON parse error: %s", e)
        return None
    try:
        return PdPatch.model_validate(data)
    except Exception as e:
        logger.warning("pd-patch schema validation failed: %s", e)
        return None


def _validate_and_canonicalize_blocks(text: str) -> tuple[str, bool]:
    """Extract pd-patch blocks, validate each, replace with canonical JSON or placeholder.

    Returns (processed_text, had_failure).
    Tracks parse failures via New Relic custom metric.
    """
    had_failure = False
    result = text
    for raw_json, full_block in _extract_pd_patches(text):
        patch = _validate_patch(raw_json)
        if patch is not None:
            canonical = json.dumps(patch.model_dump(), separators=(",", ":"), ensure_ascii=False)
            replacement = f"```pd-patch\n{canonical}\n```"
        else:
            had_failure = True
            newrelic.agent.record_custom_metric("Custom/PdPatch/ParseFailure", 1)
            replacement = "[patch diagram]"
        result = result.replace(full_block, replacement, 1)
    return result, had_failure


def chat(question: str, history: list[HistoryItem] | None = None) -> tuple[str, list[Chunk], dict, list[HistoryItem]]:
    """Single-turn entry point used by the API. Returns (answer, chunks, classification, new_history)."""
    if history is None:
        history = []

    if _check_prompt_injection(question):
        newrelic.agent.record_custom_metric("Custom/Guard/InjectionBlocked", 1)
        logger.warning("prompt injection blocked: %r", question[:120])
        return (
            "I can only answer questions about Pure Data documentation. "
            "Please ask a question about Pd objects, patching concepts, or audio techniques.",
            [], {"query_type": "conceptual"}, [],
        )

    root_meta: dict[str, Any] = {"history_turns": len(history) // 2}
    cost_token = cost_tracker.start()
    root = None
    try:
        with _start_obs_ctx("chat", as_type="span", input=question,
                            metadata=root_meta) as root:
            history = _compress_history(history)

            key = _make_cache_key(question)
            with _cache_lock:
                hit = _response_cache.get(key)

            if hit is not None:
                logger.info("cache HIT  [size=%d] q=%r", len(_response_cache), question[:80])
                _, answer, chunks, classification, extend = hit
                new_history = (
                    history + [
                        {"role": "user", "content": question},
                        {"role": "assistant", "content": _strip_patch_blocks(answer)},
                    ]
                    if extend else history
                )
                root_meta["cache"] = "hit"
                root.update(output=answer[:500], metadata=root_meta)
                return answer, chunks, classification, new_history

            logger.info("cache MISS [size=%d] q=%r", len(_response_cache), question[:80])
            classification = classify_query(question, history)
            scope_action = scope.decide(classification.get("scope"))
            if scope_action in (scope.REFUSE_OFF_TOPIC, scope.REFUSE_POLICY):
                answer = scope.refusal_text(classification.get("scope"))
                _record_scope_decision(classification.get("scope"), refused=True)
                with _cache_lock:
                    _response_cache[key] = (question, answer, [], classification, False)
                root_meta.update({
                    "cache": "miss",
                    "scope": classification.get("scope"),
                    "refused": True,
                })
                root.update(output=answer, metadata=root_meta)
                return answer, [], classification, history
            _record_scope_decision(classification.get("scope"), refused=False)
            context_chunks, classification = retrieve(
                question, history, classification=classification
            )
            if not context_chunks:
                answer = (
                    "I couldn't find any relevant Pure Data documentation for that question. "
                    "Try asking about a specific Pd object, patching concept, or audio technique."
                )
                with _cache_lock:
                    _response_cache[key] = (question, answer, [], classification, False)
                root_meta.update({"retrieved_chunks": 0, "cache": "miss"})
                root.update(output=answer, metadata=root_meta)
                return answer, [], classification, history

            answer = generate_response(
                question, context_chunks, history,
                scope_note=(
                    scope.mixed_scope_note(classification)
                    if scope.decide(classification.get("scope")) == scope.ANSWER_WITH_DECLINE
                    else None
                ),
            )
            answer, patch_failed = _validate_and_canonicalize_blocks(answer)
            if patch_failed:
                logger.info("pd-patch validation failed — retrying")
                newrelic.agent.record_custom_metric("Custom/PdPatch/Retry", 1)
                retry_answer = generate_response(_RETRY_PROMPT, context_chunks, history + [
                    {"role": "assistant", "content": answer},
                ])
                retry_answer, retry_failed = _validate_and_canonicalize_blocks(retry_answer)
                if retry_failed:
                    logger.warning("pd-patch retry also failed")
                else:
                    patch_failed = False
                answer = retry_answer
            if patch_failed and _langfuse is not None:
                try:
                    root.update(metadata={"pd_patch_valid": False})
                except Exception:
                    pass

            faithfulness = _check_faithfulness(question, answer, context_chunks)
            if faithfulness is not None and (faithfulness.get("score") or 0) <= FAITHFULNESS_RETRY_THRESHOLD:
                logger.info("low faithfulness (score=%s): %s — retrying",
                            faithfulness.get("score"), faithfulness.get("explanation"))
                newrelic.agent.record_custom_metric("Custom/Guard/FaithfulnessRetry", 1)
                retry_prompt = _FAITHFULNESS_RETRY_PROMPT.format(
                    explanation=faithfulness.get("explanation", "unsupported claims"))
                retry_answer = generate_response(retry_prompt, context_chunks, history + [
                    {"role": "assistant", "content": answer},
                ])
                retry_answer, retry_patch_failed = _validate_and_canonicalize_blocks(retry_answer)
                retry_faithfulness = _check_faithfulness(question, retry_answer, context_chunks)
                if retry_faithfulness is not None and (retry_faithfulness.get("score") or 0) <= FAITHFULNESS_RETRY_THRESHOLD:
                    logger.warning("faithfulness retry still low (score=%s)", retry_faithfulness.get("score"))
                    newrelic.agent.record_custom_metric("Custom/Guard/LowFaithfulness", 1)
                answer = retry_answer
                patch_failed = retry_patch_failed

            new_history = history + [
                {"role": "user", "content": question},
                {"role": "assistant", "content": _strip_patch_blocks(answer)},
            ]
            with _cache_lock:
                _response_cache[key] = (question, answer, context_chunks, classification, True)
            root_meta.update({"retrieved_chunks": len(context_chunks),
                              "query_type": classification["query_type"],
                              "cache": "miss"})
            root.update(output=answer[:500], metadata=root_meta)
            return answer, context_chunks, classification, new_history
    finally:
        cost_summary = cost_tracker.finish(cost_token)
        if cost_summary is not None and root is not None:
            _record_request_cost(root, root_meta, cost_summary)


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
