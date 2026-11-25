"""Request orchestration on top of the agent loop.

This is what the web endpoint, the CLI and (later) Slack all call. It adds the
things a *request* needs — the injection check, the response cache, the scope
gate, patch artifacts, the faithfulness guard, cost accounting and
tracing — and it yields the same typed events as the loop, plus a terminal
``done``. It imports no web framework and no SSE helper: turning events into
``data: {...}\\n\\n`` frames is the adapter's job (``main.py``).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import AsyncIterator

import newrelic.agent

import cost_tracker
import scope
from . import artifacts, cache, observability
from .classification import classify_query
from .events import AgentEvent, Artifact, Done, Sources, TextDelta, TextReset, ToolResult
from .guardrails import (
    FAITHFULNESS_RETRY_THRESHOLD,
    LOW_FAITHFULNESS_DISCLAIMER,
    check_faithfulness,
    check_prompt_injection,
)
from .history import HISTORY_MAX_OUTGOING, compress_history_async
from .llm import AnthropicModelClient, ModelClient
from .loop import AgentConfig, AgentLoop
from .patch_counts import repair_patch
from .patches import append_patches, strip_patch_blocks
from .prompt import agent_system_prompt
from .retrieval import chunks_to_sources
from .tools import ToolRegistry
from .tools.docs import default_tools
from .tools.patch import execution_enabled, patch_tools
from .types import Chunk, HistoryItem
from .workspace import PatchWorkspace

logger = logging.getLogger(__name__)

INJECTION_RESPONSE = (
    "I can only answer questions about Pure Data documentation. "
    "Please ask a question about Pd objects, patching concepts, or audio techniques."
)

APOLOGY = (
    "Something went wrong while answering that question. "
    "Please try again — your question was not charged twice."
)


@dataclass
class AgentAnswer:
    """A complete agent run, without the request-level machinery.

    ``answer`` is the prose followed by its patches as text, which is what a judge
    should read; ``evidence`` is what running the patches showed.
    """

    answer: str
    chunks: list[Chunk]
    turns: int = 0
    tool_calls: int = 0
    retrieval_calls: int = 0
    tool_errors: int = 0
    recovered_errors: int = 0
    unresolved_errors: int = 0
    stop_reason: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    prose: str = ""
    patches: list[dict] = field(default_factory=list)
    evidence: list[Chunk] = field(default_factory=list)
    workspace: PatchWorkspace | None = None


def default_registry(
    workspace: PatchWorkspace | None = None, *, execution: bool | None = None
) -> ToolRegistry:
    """Documentation search plus the patch tools.

    ``execution`` adds the tools that run patches; by default it follows whether
    a sandbox backend is configured.
    """
    if execution is None:
        execution = execution_enabled()
    return ToolRegistry([
        *default_tools(),
        *patch_tools(workspace or PatchWorkspace(), execution=execution),
    ])


def default_config(*, execution: bool | None = None, **overrides) -> AgentConfig:
    if execution is None:
        execution = execution_enabled()
    return AgentConfig(**{
        "system_prompt": agent_system_prompt(execution=execution),
        # Writing, running, analysing and revising a patch takes more turns.
        "max_turns": 9 if execution else 6,
        **overrides,
    })


def _finish_patches(workspace: PatchWorkspace, chunks: list[Chunk]) -> None:
    """Correct the diagrams' counts against the excerpts this run retrieved."""
    for entry in workspace.entries.values():
        entry.patch = repair_patch(entry.patch, chunks)


async def answer_question(
    question: str,
    history: list[HistoryItem] | None = None,
    *,
    client: ModelClient | None = None,
    tools: ToolRegistry | None = None,
    config: AgentConfig | None = None,
    scope_note: str | None = None,
    execution: bool | None = None,
    workspace: PatchWorkspace | None = None,
) -> AgentAnswer:
    """Run the loop to completion and return the answer.

    The evaluation harness uses this so the agent arm is measured on the same
    thing the serving path runs — the loop and its tools — without the cache and
    guardrail layer that would make repeated eval entries interfere.
    """
    workspace = workspace or PatchWorkspace()
    loop = AgentLoop(
        client or AnthropicModelClient(),
        tools or default_registry(workspace, execution=execution),
        config or default_config(execution=execution),
    )
    async for _event in loop.stream(
        question, history=history, scope_note=scope_note
    ):
        pass
    state = loop.state
    _finish_patches(workspace, list(state.chunks))
    patches = workspace.patches()
    return AgentAnswer(
        answer=append_patches(state.answer, patches),
        prose=state.answer,
        patches=patches,
        evidence=workspace.evidence(),
        workspace=workspace,
        chunks=list(state.chunks),
        turns=state.turns,
        tool_calls=state.tool_calls,
        retrieval_calls=state.retrieval_calls,
        tool_errors=state.tool_errors,
        recovered_errors=state.recovered_errors,
        unresolved_errors=state.unresolved_errors,
        stop_reason=state.stop_reason,
        usage=dict(state.usage),
    )


async def stream_chat(
    question: str,
    history: list[HistoryItem] | None = None,
    *,
    client: ModelClient | None = None,
    tools: ToolRegistry | None = None,
    config: AgentConfig | None = None,
    workspace: PatchWorkspace | None = None,
) -> AsyncIterator[AgentEvent]:
    """Answer one question, yielding the typed event stream ending in ``done``."""
    history = list(history or [])

    if check_prompt_injection(question):
        newrelic.agent.record_custom_metric("Custom/Guard/InjectionBlocked", 1)
        logger.warning("prompt injection blocked: %r", question[:120])
        yield Sources(items=(), query_type="conceptual")
        yield TextDelta(INJECTION_RESPONSE)
        yield Done(
            answer=INJECTION_RESPONSE,
            new_history=history,
            query_type="conceptual",
            refused=True,
        )
        return

    root_meta: dict = {"history_turns": len(history) // 2, "engine": "agent"}
    root = observability.start_obs("chat", as_type="span", input=question, metadata=root_meta)
    cost_token = cost_tracker.start()

    try:
        history = await compress_history_async(history)

        hit = cache.get(question)
        if hit is not None:
            newrelic.agent.record_custom_metric("Custom/Cache/Hit", 1)
            logger.info("cache HIT  [size=%d] q=%r", cache.stats()["size"], question[:80])
            new_history = (
                history + [
                    {"role": "user", "content": question},
                    {"role": "assistant", "content": strip_patch_blocks(hit.answer)},
                ]
                if hit.extend_history else history
            )
            root_meta["cache"] = "hit"
            root.update(output=hit.answer[:500], metadata=root_meta)
            yield Sources(
                items=tuple(chunks_to_sources(hit.chunks)),
                query_type=hit.query_type,
            )
            yield TextDelta(hit.answer)
            for item in hit.patches:
                yield Artifact(kind="pd_patch", data=item)
            for item in hit.audio:
                if artifacts.get(item["id"]) is not None:
                    yield Artifact(kind="audio", data=item)
            yield Done(
                answer=hit.answer,
                patches=hit.patches,
                new_history=new_history[-HISTORY_MAX_OUTGOING:],
                chunks=tuple(hit.chunks),
                query_type=hit.query_type,
                cached=True,
            )
            return

        newrelic.agent.record_custom_metric("Custom/Cache/Miss", 1)
        logger.info("cache MISS [size=%d] q=%r", cache.stats()["size"], question[:80])

        classification = await asyncio.to_thread(classify_query, question, history)
        query_type = classification.get("query_type", "both")
        scope_action = scope.decide(classification.get("scope"))

        # Refusals are a policy decision, not a tool the model may skip.
        if scope_action in (scope.REFUSE_OFF_TOPIC, scope.REFUSE_POLICY):
            answer = scope.refusal_text(classification.get("scope"))
            observability.record_scope_decision(classification.get("scope"), refused=True)
            cache.put(question, answer, [], query_type, extend_history=False)
            root_meta.update({
                "cache": "miss",
                "scope": classification.get("scope"),
                "refused": True,
            })
            root.update(output=answer, metadata=root_meta)
            yield Sources(items=(), query_type=query_type)
            yield TextDelta(answer)
            yield Done(
                answer=answer,
                new_history=history,
                query_type=query_type,
                refused=True,
            )
            return

        observability.record_scope_decision(classification.get("scope"), refused=False)

        scope_note = (
            scope.mixed_scope_note(classification)
            if scope_action == scope.ANSWER_WITH_DECLINE
            else None
        )

        workspace = workspace or PatchWorkspace()
        loop = AgentLoop(
            client or AnthropicModelClient(),
            tools or default_registry(workspace),
            config or default_config(),
        )
        streamed: list[str] = []
        rejected_patch = False
        sources_seen = False
        try:
            async for event in loop.stream(
                question, history=history, scope_note=scope_note
            ):
                # The loop answers "what did we find"; the request layer knows
                # "how was this question routed". Attach the routing label to the
                # citations so adapters can emit their meta frame before text.
                if isinstance(event, Sources):
                    sources_seen = True
                    yield Sources(items=event.items, query_type=query_type)
                else:
                    if isinstance(event, TextDelta):
                        streamed.append(event.text)
                    elif isinstance(event, TextReset):
                        streamed.clear()
                    elif isinstance(event, ToolResult) and event.data.get("invalid_patch"):
                        rejected_patch = True
                    yield event
        except Exception:
            # A failed run must still terminate the stream with a usable event;
            # the loop can fail on a provider error mid-answer.
            logger.exception("agent run failed")
            newrelic.agent.record_custom_metric("Custom/Agent/RunError", 1)
            if not sources_seen:
                yield Sources(items=(), query_type=query_type)
            yield TextDelta(APOLOGY)
            answer = "".join(streamed) + APOLOGY
            yield Done(
                answer=answer,
                new_history=history,
                query_type=query_type,
                turns=loop.state.turns,
                tool_calls=loop.state.tool_calls,
            )
            return

        state = loop.state
        answer = state.answer
        _finish_patches(workspace, list(state.chunks))
        patches = tuple(workspace.patches())
        audio = tuple(workspace.audio())
        # The model tried to write a patch and never produced a valid one.
        patch_failed = rejected_patch and not patches
        if patch_failed:
            newrelic.agent.record_custom_metric("Custom/Agent/PdPatchInvalid", 1)
            if observability.langfuse() is not None:
                try:
                    observability.langfuse().score_current_span(name="pd_patch_valid", value=0)
                except Exception:
                    pass

        for item in patches:
            yield Artifact(kind="pd_patch", data=item)
        for item in audio:
            yield Artifact(kind="audio", data=item)

        # Faithfulness cannot gate a retry on the streaming path — the answer is
        # already on the wire — so a low score appends a visible disclaimer.
        faithfulness = await asyncio.to_thread(
            check_faithfulness, question, append_patches(answer, patches),
            list(state.chunks) + workspace.evidence() if state.chunks else [],
        )
        if faithfulness is not None and (
            faithfulness.get("score") or 0
        ) <= FAITHFULNESS_RETRY_THRESHOLD:
            newrelic.agent.record_custom_metric("Custom/Guard/LowFaithfulness", 1)
            logger.info(
                "low faithfulness (streaming, score=%s): %s",
                faithfulness.get("score"), faithfulness.get("explanation"),
            )
            if observability.langfuse() is not None:
                try:
                    observability.langfuse().score_current_span(
                        name="faithfulness", value=faithfulness.get("score")
                    )
                except Exception:
                    pass
            answer += LOW_FAITHFULNESS_DISCLAIMER
            yield TextDelta(LOW_FAITHFULNESS_DISCLAIMER)

        extend_history = bool(state.chunks)
        new_history = (
            history + [
                {"role": "user", "content": question},
                {"role": "assistant", "content": strip_patch_blocks(answer)},
            ]
            if extend_history else history
        )
        cache.put(
            question, answer, list(state.chunks), query_type, extend_history,
            patches=patches, audio=audio,
        )

        root_meta.update({
            "cache": "miss",
            "scope": classification.get("scope"),
            "query_type": query_type,
            "turns": state.turns,
            "tool_calls": state.tool_calls,
            "retrieval_calls": state.retrieval_calls,
            "retrieved_chunks": len(state.chunks),
            "stop_reason": state.stop_reason,
            "pd_patch_failed": patch_failed,
            "patches": len(patches),
            "patch_runs": workspace.runs,
        })
        root.update(output=answer[:500], metadata=root_meta)
        yield Done(
            answer=answer,
            new_history=new_history[-HISTORY_MAX_OUTGOING:],
            chunks=tuple(state.chunks),
            query_type=query_type,
            turns=state.turns,
            tool_calls=state.tool_calls,
            patch_failed=patch_failed,
            patches=patches,
        )
    finally:
        cost_summary = cost_tracker.finish(cost_token)
        if cost_summary is not None:
            observability.record_request_cost(root, root_meta, cost_summary)
        root.end()
