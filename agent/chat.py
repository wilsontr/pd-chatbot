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
import time
import uuid
from dataclasses import dataclass, field
from typing import AsyncIterator, Collection

import newrelic.agent

import cost_tracker
import scope
from pd_schema import PdPatch

from . import artifacts, cache, observability
from .classification import classify_query
from .events import AgentEvent, Artifact, Done, Sources, TextDelta, TextReset, ToolResult
from .guardrails import (
    FAITHFULNESS_RETRY_THRESHOLD,
    LOW_FAITHFULNESS_DISCLAIMER,
    ToolPolicy,
    check_faithfulness,
    check_prompt_injection,
    policy_for,
)
from .history import HISTORY_MAX_OUTGOING, compress_history_async
from .llm import AnthropicModelClient, ModelClient
from .loop import AgentConfig, AgentLoop
from .patch_counts import repair_patch
from .patches import append_patches, strip_patch_blocks
from .prompt import agent_system_prompt
from .retrieval import chunks_to_sources
from .sessions import SessionPatchStore
from .store import RunRecorder, RunStore
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
    budget_exceeded: str | None = None
    stop_reason: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    prose: str = ""
    patches: list[dict] = field(default_factory=list)
    evidence: list[Chunk] = field(default_factory=list)
    workspace: PatchWorkspace | None = None


def default_registry(
    workspace: PatchWorkspace | None = None,
    *,
    execution: bool | None = None,
    interface: str = "web",
    policy: ToolPolicy | None = None,
    approved_tools: Collection[str] = (),
) -> ToolRegistry:
    """Documentation search plus the patch tools permitted for ``interface``.

    ``execution`` adds the tools that run patches; by default it follows whether
    a sandbox backend is configured, and is further constrained by the
    interface's ``ToolPolicy``.
    """
    if execution is None:
        execution = execution_enabled()
    effective_policy = policy or policy_for(interface)
    registry = ToolRegistry([
        *default_tools(),
        *patch_tools(workspace or PatchWorkspace(), execution=execution),
    ])
    return registry.filtered(effective_policy.allowed)


def default_config(
    *,
    execution: bool | None = None,
    interface: str = "web",
    policy: ToolPolicy | None = None,
    approved_tools: Collection[str] = (),
    **overrides,
) -> AgentConfig:
    if execution is None:
        execution = execution_enabled()
    effective_policy = policy or policy_for(interface)
    approved = frozenset(approved_tools)
    can_execute = execution and bool(
        effective_policy.permitted_names(approved=approved) & {"run_patch", "analyze_audio"}
    )
    return AgentConfig(**{
        "system_prompt": agent_system_prompt(execution=can_execute),
        # Writing, running, analysing and revising a patch takes more turns.
        "max_turns": 9 if can_execute else 6,
        "tool_policy": effective_policy,
        "approved_tools": approved,
        **overrides,
    })


def _finish_patches(workspace: PatchWorkspace, chunks: list[Chunk]) -> None:
    """Correct the diagrams this run wrote against the excerpts it retrieved.

    Earlier turns' patches are left alone: this run's excerpts are evidence about
    the question that was asked now, not about a patch from another turn.
    """
    for entry in workspace.entries.values():
        if entry.touched:
            entry.patch = repair_patch(entry.patch, chunks)


def _adopt_cached_patches(
    store: SessionPatchStore,
    session_id: str,
    cached: Collection[dict],
    workspace: PatchWorkspace | None,
) -> list[dict]:
    """Save a cached answer's patches into this session and return their current data.

    The response cache is keyed by question and shared between sessions, so a
    cached artifact's ids belong to whichever session filled the cache. Re-saving
    under the current session mints ids for this session; a patch the session
    already has keeps its newer revision instead of being overwritten by the
    cached copy.
    """
    ws = workspace or PatchWorkspace()
    store.seed_workspace(session_id, ws)
    names: list[str] = []
    for item in cached:
        name = str(item.get("name") or "")
        if not name:
            continue
        names.append(name)
        if name in ws.entries:
            continue
        try:
            patch = PdPatch.model_validate(item.get("patch"))
        except Exception:  # cached data is our own, but never fail an answer on it
            logger.warning("skipping malformed cached patch %r", name)
            continue
        entry = ws.write(name, patch)
        entry.revisions = max(1, int(item.get("revision") or 1))
    store.save_workspace(session_id, ws)
    return [ws.entries[name].to_artifact() for name in names if name in ws.entries]


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
    interface: str = "eval",
    policy: ToolPolicy | None = None,
    approved_tools: Collection[str] = (),
) -> AgentAnswer:
    """Run the loop to completion and return the answer.

    The evaluation harness uses this so the agent arm is measured on the same
    thing the serving path runs — the loop and its tools — without the cache and
    guardrail layer that would make repeated eval entries interfere.
    """
    workspace = workspace or PatchWorkspace()
    loop = AgentLoop(
        client or AnthropicModelClient(),
        tools or default_registry(
            workspace, execution=execution, interface=interface,
            policy=policy, approved_tools=approved_tools,
        ),
        config or default_config(
            execution=execution, interface=interface,
            policy=policy, approved_tools=approved_tools,
        ),
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
        budget_exceeded=state.budget_exceeded,
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
    interface: str = "web",
    policy: ToolPolicy | None = None,
    approved_tools: Collection[str] = (),
    run_id: str | None = None,
    session_id: str | None = None,
    run_store: RunStore | None = None,
    patch_store: SessionPatchStore | None = None,
) -> AsyncIterator[AgentEvent]:
    """Answer one question, yielding the typed event stream ending in ``done``.

    With a ``patch_store`` the workspace is seeded from the patches already
    stored for ``session_id`` and the ones written this turn are saved back, so a
    follow-up can edit the previous turn's patch.
    """
    history = list(history or [])
    run_id = run_id or uuid.uuid4().hex
    session_id = session_id or uuid.uuid4().hex
    recorder = RunRecorder(run_store, run_id=run_id, session_id=session_id)
    run_t0 = time.perf_counter()

    def _elapsed_ms() -> float:
        return round((time.perf_counter() - run_t0) * 1000, 2)

    def _current_cost_usd(fallback: float | None = None) -> float | None:
        tracker = cost_tracker.current()
        if tracker is not None and tracker.calls:
            return round(tracker.total_usd, 8)
        return fallback

    if check_prompt_injection(question):
        newrelic.agent.record_custom_metric("Custom/Guard/InjectionBlocked", 1)
        logger.warning("prompt injection blocked: %r", question[:120])
        await recorder.start(question, interface=interface)
        ev_sources = Sources(items=(), query_type="conceptual")
        await recorder.record(ev_sources)
        yield ev_sources
        ev_text = TextDelta(INJECTION_RESPONSE)
        await recorder.record(ev_text)
        yield ev_text
        ev_done = Done(
            answer=INJECTION_RESPONSE,
            new_history=history,
            query_type="conceptual",
            refused=True,
            run_id=run_id,
            session_id=session_id,
        )
        await recorder.record(ev_done)
        await recorder.finish(
            status="refused",
            query_type="conceptual",
            answer=INJECTION_RESPONSE,
            refused=True,
            latency_ms=_elapsed_ms(),
        )
        yield ev_done
        return

    root_meta: dict = {
        "history_turns": len(history) // 2,
        "engine": "agent",
        "interface": interface,
        "run_id": run_id,
        "session_id": session_id,
    }
    root = observability.start_obs("chat", as_type="span", input=question, metadata=root_meta)
    lf_trace_id = observability.trace_id(root)
    lf_trace_url = observability.trace_url(root)
    lf_trace_ctx = observability.trace_context(root)
    cost_token = cost_tracker.start()
    finished_log = False

    await recorder.start(
        question,
        interface=interface,
        model=config.model if config is not None else "",
        langfuse_trace_id=lf_trace_id,
        langfuse_trace_url=lf_trace_url,
    )

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
            ev_sources = Sources(
                items=tuple(chunks_to_sources(hit.chunks)),
                query_type=hit.query_type,
            )
            await recorder.record(ev_sources)
            yield ev_sources
            ev_text = TextDelta(hit.answer)
            await recorder.record(ev_text)
            yield ev_text
            hit_patches = (
                await asyncio.to_thread(
                    _adopt_cached_patches, patch_store, session_id, hit.patches, workspace
                )
                if patch_store is not None
                else list(hit.patches)
            )
            for item in hit_patches:
                ev_art = Artifact(kind="pd_patch", data=item)
                await recorder.record(ev_art)
                yield ev_art
            for item in hit.audio:
                if artifacts.get(item["id"]) is not None:
                    ev_art = Artifact(kind="audio", data=item)
                    await recorder.record(ev_art)
                    yield ev_art
            ev_done = Done(
                answer=hit.answer,
                patches=tuple(hit_patches),
                new_history=new_history[-HISTORY_MAX_OUTGOING:],
                chunks=tuple(hit.chunks),
                query_type=hit.query_type,
                cached=True,
                run_id=run_id,
                session_id=session_id,
                langfuse_trace_id=lf_trace_id,
                langfuse_trace_url=lf_trace_url,
            )
            await recorder.record(ev_done)
            await recorder.finish(
                status="completed",
                query_type=hit.query_type,
                answer=hit.answer,
                cached=True,
                cost_usd=_current_cost_usd(),
                latency_ms=_elapsed_ms(),
            )
            finished_log = True
            yield ev_done
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
            ev_sources = Sources(items=(), query_type=query_type)
            await recorder.record(ev_sources)
            yield ev_sources
            ev_text = TextDelta(answer)
            await recorder.record(ev_text)
            yield ev_text
            ev_done = Done(
                answer=answer,
                new_history=history,
                query_type=query_type,
                refused=True,
                run_id=run_id,
                session_id=session_id,
                langfuse_trace_id=lf_trace_id,
                langfuse_trace_url=lf_trace_url,
            )
            await recorder.record(ev_done)
            await recorder.finish(
                status="refused",
                query_type=query_type,
                answer=answer,
                refused=True,
                cost_usd=_current_cost_usd(),
                latency_ms=_elapsed_ms(),
            )
            finished_log = True
            yield ev_done
            return

        observability.record_scope_decision(classification.get("scope"), refused=False)

        scope_note = (
            scope.mixed_scope_note(classification)
            if scope_action == scope.ANSWER_WITH_DECLINE
            else None
        )

        workspace = workspace or PatchWorkspace()
        if patch_store is not None:
            # The session's existing patches come first, so the tools can read and
            # edit them; only this turn's writes are persisted back later.
            await asyncio.to_thread(patch_store.seed_workspace, session_id, workspace)
        effective_config = config or default_config(
            interface=interface, policy=policy, approved_tools=approved_tools,
        )
        loop = AgentLoop(
            client or AnthropicModelClient(),
            tools or default_registry(
                workspace, interface=interface, policy=policy,
                approved_tools=approved_tools,
            ),
            effective_config,
        )
        streamed: list[str] = []
        rejected_patch = False
        sources_seen = False
        try:
            async for event in loop.stream(
                question, history=history, scope_note=scope_note, trace_context=lf_trace_ctx
            ):
                # The loop answers "what did we find"; the request layer knows
                # "how was this question routed". Attach the routing label to the
                # citations so adapters can emit their meta frame before text.
                if isinstance(event, Sources):
                    sources_seen = True
                    emitted: AgentEvent = Sources(items=event.items, query_type=query_type)
                else:
                    if isinstance(event, TextDelta):
                        streamed.append(event.text)
                    elif isinstance(event, TextReset):
                        streamed.clear()
                    elif isinstance(event, ToolResult) and event.data.get("invalid_patch"):
                        rejected_patch = True
                    emitted = event
                await recorder.record(emitted)
                yield emitted
        except Exception:
            # A failed run must still terminate the stream with a usable event;
            # the loop can fail on a provider error mid-answer.
            logger.exception("agent run failed")
            newrelic.agent.record_custom_metric("Custom/Agent/RunError", 1)
            if not sources_seen:
                ev_sources = Sources(items=(), query_type=query_type)
                await recorder.record(ev_sources)
                yield ev_sources
            ev_apology = TextDelta(APOLOGY)
            await recorder.record(ev_apology)
            yield ev_apology
            answer = "".join(streamed) + APOLOGY
            ev_done = Done(
                answer=answer,
                new_history=history,
                query_type=query_type,
                turns=loop.state.turns,
                tool_calls=loop.state.tool_calls,
                run_id=run_id,
                session_id=session_id,
                langfuse_trace_id=lf_trace_id,
                langfuse_trace_url=lf_trace_url,
            )
            await recorder.record(ev_done)
            await recorder.finish(
                status="failed",
                query_type=query_type,
                answer=answer,
                model=effective_config.model,
                turns=loop.state.turns,
                tool_calls=loop.state.tool_calls,
                total_tokens=loop.state.total_tokens,
                cost_usd=_current_cost_usd(loop.state.cost_usd or None),
                latency_ms=_elapsed_ms(),
                stop_reason="error",
            )
            finished_log = True
            yield ev_done
            return

        state = loop.state
        answer = state.answer
        _finish_patches(workspace, list(state.chunks))
        if patch_store is not None:
            # Ids and download URLs come from the store, so persist before the
            # artifacts are built.
            await asyncio.to_thread(patch_store.save_workspace, session_id, workspace)
        # Only the patches this turn wrote: earlier turns' patches were drawn
        # under their own answers and are listed for the session instead.
        patches = tuple(workspace.patches(answered_only=True))
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
            ev_art = Artifact(kind="pd_patch", data=item)
            await recorder.record(ev_art)
            yield ev_art
        for item in audio:
            ev_art = Artifact(kind="audio", data=item)
            await recorder.record(ev_art)
            yield ev_art

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
            ev_disc = TextDelta(LOW_FAITHFULNESS_DISCLAIMER)
            await recorder.record(ev_disc)
            yield ev_disc

        extend_history = bool(state.chunks) and not state.budget_exceeded
        new_history = (
            history + [
                {"role": "user", "content": question},
                {"role": "assistant", "content": strip_patch_blocks(answer)},
            ]
            if extend_history else history
        )
        if not state.budget_exceeded:
            cache.put(
                question, answer, list(state.chunks), query_type, extend_history,
                patches=patches, audio=audio,
            )

        root_meta.update({
            "cache": "miss",
            "interface": interface,
            "scope": classification.get("scope"),
            "query_type": query_type,
            "turns": state.turns,
            "tool_calls": state.tool_calls,
            "retrieval_calls": state.retrieval_calls,
            "retrieved_chunks": len(state.chunks),
            "stop_reason": state.stop_reason,
            "budget_exceeded": state.budget_exceeded,
            "disallowed_tool_calls": state.disallowed_tool_calls,
            "blocked_tool_outputs": state.blocked_tool_outputs,
            "pd_patch_failed": patch_failed,
            "patches": len(patches),
            "patch_runs": workspace.runs,
        })
        root.update(output=answer[:500], metadata=root_meta)
        ev_done = Done(
            answer=answer,
            new_history=new_history[-HISTORY_MAX_OUTGOING:],
            chunks=tuple(state.chunks),
            query_type=query_type,
            turns=state.turns,
            tool_calls=state.tool_calls,
            patch_failed=patch_failed,
            budget_exceeded=state.budget_exceeded,
            patches=patches,
            run_id=run_id,
            session_id=session_id,
            langfuse_trace_id=lf_trace_id,
            langfuse_trace_url=lf_trace_url,
        )
        await recorder.record(ev_done)
        await recorder.finish(
            status="completed",
            query_type=query_type,
            answer=answer,
            model=effective_config.model,
            turns=state.turns,
            tool_calls=state.tool_calls,
            total_tokens=state.total_tokens,
            cost_usd=_current_cost_usd(state.cost_usd or None),
            latency_ms=_elapsed_ms(),
            stop_reason=state.stop_reason,
            budget_exceeded=state.budget_exceeded,
        )
        finished_log = True
        yield ev_done
    finally:
        if not finished_log:
            await recorder.flush()
        cost_summary = cost_tracker.finish(cost_token)
        if cost_summary is not None:
            observability.record_request_cost(root, root_meta, cost_summary)
        root.end()
