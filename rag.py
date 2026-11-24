# rag.py
"""The fixed RAG pipeline: classify, retrieve, generate, validate.

This module is the **baseline arm** of the evaluation. The serving path is the
agent core in ``agent/``, where retrieval is a tool the model calls; this
pipeline still runs the same retrieval and generation but in a fixed order, so
the eval report can compare the two on the same golden set
(``python -m evaluation.evaluate --generation --mode both``).

Everything shared lives in ``agent/`` and is imported here under the names this
module has always used (``classify_query``, ``retrieve``, ``_check_faithfulness``,
…) so the existing eval harness and tests keep working unchanged.
"""

import asyncio
import logging

import newrelic.agent

import cost_tracker
import scope
from model_config import ANSWER_MODEL, max_tokens_for, thinking_kwargs
from llm_response import text_of
from evaluation.retry import with_retries

from agent.cache import (
    get as cache_get,
    put as cache_put,
    response_cache,
    stats as cache_stats,
)
from agent.classification import classify_query
from agent.guardrails import (
    FAITHFULNESS_RETRY_PROMPT,
    FAITHFULNESS_RETRY_THRESHOLD,
    check_faithfulness,
    check_prompt_injection,
)
from agent.history import (
    HISTORY_COMPRESS_AFTER,
    HISTORY_MAX_OUTGOING,
    compress_history,
)
from agent.llm import async_client, sync_client
from agent.observability import (
    cost_details,
    langfuse,
    record_model_call,
    record_request_cost,
    record_scope_decision,
    start_obs,
    start_obs_ctx,
    token_usage,
)
from agent.patches import (
    RETRY_PROMPT,
    extract_pd_patches,
    strip_patch_blocks,
    validate_and_canonicalize_blocks,
    validate_patch,
)
from agent.prompt import SYSTEM_PROMPT
from agent.retrieval import (
    bm25,
    bm25_search,
    child_chunks,
    chunks_to_sources,
    collection,
    format_excerpts,
    hybrid_retrieve,
    max_similarity,
    object_names_from_question,
    parent_lookup,
    search_documents,
    vc,
    vector_search,
)
from agent.types import Chunk, HistoryItem

logger = logging.getLogger(__name__)

# --- Back-compatible names -------------------------------------------------------
# Tests and the evaluation harness import these from rag; they live in agent/ now.
_response_cache = response_cache
_langfuse = langfuse()
_token_usage = token_usage
_cost_details = cost_details
_start_obs = start_obs
_start_obs_ctx = start_obs_ctx
_chunks_to_sources = chunks_to_sources
_check_prompt_injection = check_prompt_injection
_check_faithfulness = check_faithfulness
_compress_history = compress_history
_record_scope_decision = record_scope_decision
_record_request_cost = record_request_cost
_strip_patch_blocks = strip_patch_blocks
_extract_pd_patches = extract_pd_patches
_validate_patch = validate_patch
_validate_and_canonicalize_blocks = validate_and_canonicalize_blocks

__all__ = [
    "ANSWER_MODEL",
    "SYSTEM_PROMPT",
    "HISTORY_COMPRESS_AFTER",
    "HISTORY_MAX_OUTGOING",
    "HistoryItem",
    "Chunk",
    "bm25",
    "bm25_search",
    "child_chunks",
    "classify_query",
    "collection",
    "generate_response",
    "hybrid_retrieve",
    "max_similarity",
    "object_names_from_question",
    "parent_lookup",
    "retrieve",
    "search_documents",
    "vc",
    "vector_search",
    "async_stream_chat",
    "chat",
]


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
    context_str = format_excerpts(context_chunks)

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
            sync_client().messages.create,
            model=ANSWER_MODEL,
            max_tokens=max_tokens_for(ANSWER_MODEL),
            system=system_block,
            messages=messages,
            **thinking_kwargs(ANSWER_MODEL),
        )
        obs.update(output=text_of(response), usage_details=_token_usage(response))
        if _token_usage(response):
            obs.update(cost_details=_cost_details(ANSWER_MODEL, response))
        record_model_call("generate", ANSWER_MODEL, _token_usage(response))
    return text_of(response)


@newrelic.agent.function_trace()
async def _async_generate_response_stream(
    question: str,
    context_chunks: list[Chunk],
    history: list[HistoryItem],
    _usage_capture: list | None = None,
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
        async with async_client().messages.stream(
            model=ANSWER_MODEL,
            max_tokens=max_tokens_for(ANSWER_MODEL),
            system=system_block,
            messages=messages,
            **thinking_kwargs(ANSWER_MODEL),
        ) as stream:
            async for text in stream.text_stream:
                yield text
            final = await stream.get_final_message()
            record_model_call("generate", ANSWER_MODEL, _token_usage(final))
            if _usage_capture is not None:
                _usage_capture.append(final)


@newrelic.agent.function_trace()
def retrieve(
    question: str,
    history: list[HistoryItem] | None = None,
    *,
    classification: dict | None = None,
) -> tuple[list[Chunk], dict]:
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
        outcome = search_documents(
            query,
            query_type=query_type,
            object_names=object_names,
            bm25_query=bm25_query,
        )
        obs.update(
            output={"chunk_count": len(outcome.chunks),
                    "headings": [c.get("heading_path", "")[:120] for c in outcome.chunks[:5]]},
            metadata={"query_type": query_type, "object_names": object_names,
                      "chunk_sources": [c.get("source", "") for c in outcome.chunks[:5]],
                      "best_similarity": round(outcome.best_similarity, 3) if outcome.best_similarity is not None else None,
                      "below_relevance_cutoff": outcome.below_cutoff},
        )
    return outcome.chunks, classification


async def async_stream_chat(question: str, history: list[HistoryItem] | None = None):
    """Async generator entry point for the pipeline arm. Yields event dicts."""
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

    root_meta: dict = {"history_turns": len(history) // 2}
    root = _start_obs("chat", as_type="span", input=question, metadata=root_meta)
    cost_token = cost_tracker.start()

    try:
        history = await asyncio.to_thread(_compress_history, history)

        hit = cache_get(question)

        if hit is not None:
            newrelic.agent.record_custom_metric('Custom/Cache/Hit', 1)
            logger.info("cache HIT  [size=%d] q=%r", cache_stats()["size"], question[:80])
            new_history = (
                history + [
                    {"role": "user", "content": question},
                    {"role": "assistant", "content": _strip_patch_blocks(hit.answer)},
                ]
                if hit.extend_history else history
            )
            root_meta["cache"] = "hit"
            root.update(output=hit.answer[:500], metadata=root_meta)
            yield {"type": "meta", "sources": _chunks_to_sources(hit.chunks), "query_type": hit.query_type}
            yield {"type": "chunk", "text": hit.answer}
            yield {"type": "done", "history": new_history[-HISTORY_MAX_OUTGOING:]}
            return

        newrelic.agent.record_custom_metric('Custom/Cache/Miss', 1)
        logger.info("cache MISS [size=%d] q=%r", cache_stats()["size"], question[:80])
        classification = await asyncio.to_thread(classify_query, question, history)
        query_type = classification["query_type"]
        scope_action = scope.decide(classification.get("scope"))
        if scope_action in (scope.REFUSE_OFF_TOPIC, scope.REFUSE_POLICY):
            answer = scope.refusal_text(classification.get("scope"))
            _record_scope_decision(classification.get("scope"), refused=True)
            cache_put(question, answer, [], query_type, extend_history=False)
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
            cache_put(question, answer, [], query_type, extend_history=False)
            root_meta.update({"retrieved_chunks": 0, "cache": "miss"})
            root.update(output=answer, metadata=root_meta)
            yield {"type": "meta", "sources": [], "query_type": query_type}
            yield {"type": "chunk", "text": answer}
            yield {"type": "done", "history": history}
            return

        yield {"type": "meta", "sources": _chunks_to_sources(context_chunks), "query_type": query_type}

        gen = _start_obs("generate_response", as_type="generation",
                         model=ANSWER_MODEL,
                         input=question,
                         metadata={"chunk_count": len(context_chunks)})
        full_text = ""
        usage_capture: list = []
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
        cache_put(question, full_text, context_chunks, query_type, extend_history=True)
        root_meta.update({"retrieved_chunks": len(context_chunks),
                          "query_type": query_type,
                          "cache": "miss"})
        root.update(output=full_text[:500], metadata=root_meta)
        yield {"type": "done", "history": new_history[-HISTORY_MAX_OUTGOING:]}

    finally:
        cost_summary = cost_tracker.finish(cost_token)
        if cost_summary is not None:
            _record_request_cost(root, root_meta, cost_summary)
        root.end()


def chat(question: str, history: list[HistoryItem] | None = None) -> tuple[str, list[Chunk], dict, list[HistoryItem]]:
    """Single-turn entry point for the pipeline arm. Returns (answer, chunks, classification, new_history)."""
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

    root_meta: dict = {"history_turns": len(history) // 2}
    cost_token = cost_tracker.start()
    root = None
    try:
        with _start_obs_ctx("chat", as_type="span", input=question,
                            metadata=root_meta) as root:
            history = _compress_history(history)

            hit = cache_get(question)

            if hit is not None:
                logger.info("cache HIT  [size=%d] q=%r", cache_stats()["size"], question[:80])
                new_history = (
                    history + [
                        {"role": "user", "content": question},
                        {"role": "assistant", "content": _strip_patch_blocks(hit.answer)},
                    ]
                    if hit.extend_history else history
                )
                root_meta["cache"] = "hit"
                root.update(output=hit.answer[:500], metadata=root_meta)
                return (
                    hit.answer, list(hit.chunks),
                    {"query_type": hit.query_type, "scope": scope.IN_SCOPE},
                    new_history,
                )

            logger.info("cache MISS [size=%d] q=%r", cache_stats()["size"], question[:80])
            classification = classify_query(question, history)
            query_type = classification["query_type"]
            scope_action = scope.decide(classification.get("scope"))
            if scope_action in (scope.REFUSE_OFF_TOPIC, scope.REFUSE_POLICY):
                answer = scope.refusal_text(classification.get("scope"))
                _record_scope_decision(classification.get("scope"), refused=True)
                cache_put(question, answer, [], query_type, extend_history=False)
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
                cache_put(question, answer, [], query_type, extend_history=False)
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
                retry_answer = generate_response(RETRY_PROMPT, context_chunks, history + [
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
                retry_prompt = FAITHFULNESS_RETRY_PROMPT.format(
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
            cache_put(question, answer, context_chunks, query_type, extend_history=True)
            root_meta.update({"retrieved_chunks": len(context_chunks),
                              "query_type": query_type,
                              "cache": "miss"})
            root.update(output=answer[:500], metadata=root_meta)
            return answer, context_chunks, classification, new_history
    finally:
        cost_summary = cost_tracker.finish(cost_token)
        if cost_summary is not None and root is not None:
            _record_request_cost(root, root_meta, cost_summary)


def main():
    """Interactive chat loop for the pipeline arm."""
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
