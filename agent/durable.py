"""Durable agent execution backed by DBOS Transact.

Wraps the agent loop in a DBOS workflow (``workflow_id == run_id``) where every
model turn, tool execution, and approval transition is a checkpointed
``@DBOS.step()``. This provides two guarantees on top of ``RunStore``:

1. **Crash / redeploy recovery**: if the process restarts mid-run, DBOS
   replays completed steps from Postgres (or SQLite locally) and resumes from
   the last unfinished step without repeating finished LLM turns or tool calls.
2. **Human-in-the-loop approval**: tools declared in
   ``ToolPolicy.require_approval`` (e.g. headless Pd sandbox runs from Slack)
   pause the workflow durably on ``DBOS.recv_async`` until ``approve_run()``
   delivers an approval or denial via ``DBOS.send_async``.

See ``backlog/decisions/decision-1 - Use-DBOS-Transact-for-durable-agent-runs.md``
for the engine choice and trade-offs.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from dataclasses import dataclass, replace
from typing import Any, AsyncIterator, Callable, Collection

from dbos import DBOS, DBOSConfig, SetWorkflowID, WorkflowHandleAsync

from . import observability
from .events import (
    ApprovalRequested,
    ApprovalResolved,
    Artifact,
    Done,
    Sources,
    TextDelta,
)
from .guardrails import (
    FAITHFULNESS_RETRY_THRESHOLD,
    LOW_FAITHFULNESS_DISCLAIMER,
    ToolPolicy,
    check_faithfulness,
    check_prompt_injection,
    policy_for,
    run_guarded_tool,
)
from .history import HISTORY_MAX_OUTGOING
from .llm import (
    AnthropicModelClient,
    ModelClient,
    ModelDelta,
    ModelRequest,
    ModelTurn,
    ToolCallRequest,
)
from .loop import AgentConfig, AgentLoop, trace_turn
from .patch_counts import repair_patch
from .patches import append_patches, strip_patch_blocks
from .sessions import SessionPatchStore
from .store import RunStore, event_to_record
from .tools import ToolOutcome, ToolRegistry
from .types import Chunk, HistoryItem
from .workspace import PatchWorkspace
import scope

logger = logging.getLogger(__name__)

APPROVAL_TOPIC = "approval"
PENDING_APPROVAL_KEY = "pending_approval"
_DEFAULT_DBOS_SQLITE_URL = "sqlite:///dbos_system.db"
_INJECTION_RESPONSE = (
    "I can only answer questions about Pure Data documentation. "
    "Please ask a question about Pd objects, patching concepts, or audio techniques."
)


@dataclass
class DurableRuntime:
    """Process-local factories resolved inside DBOS steps.

    DBOS checkpoints workflow arguments and step outputs in Postgres/SQLite, so
    live non-serializable objects (API clients, tool registries, DB stores) are
    resolved through this runtime container rather than passed as workflow args.
    """

    client_factory: Callable[[], ModelClient]
    tools_factory: Callable[
        [PatchWorkspace, str, ToolPolicy | None, Collection[str]], ToolRegistry
    ]
    config_factory: Callable[
        [str, ToolPolicy | None, Collection[str]], AgentConfig
    ]
    run_store: RunStore | None = None
    classify_fn: Callable[[str, list[HistoryItem]], dict[str, Any]] | None = None
    #: Session patch store: the durable loop seeds its workspace from it and
    #: saves the patches each run wrote back to it (TASK-5.12).
    patch_store: SessionPatchStore | None = None
    #: The faithfulness guard run on the finished answer, as on the streaming
    #: path; None skips it (tests).
    faithfulness_fn: Callable[[str, str, list[Chunk]], dict[str, Any] | None] | None = None


_runtime: DurableRuntime | None = None
_launched: bool = False


def default_runtime(
    run_store: RunStore | None = None,
    patch_store: SessionPatchStore | None = None,
) -> DurableRuntime:
    """Production runtime wired to Anthropic, default tools, and classifier."""
    from .chat import default_config, default_registry
    from .classification import classify_query

    return DurableRuntime(
        client_factory=AnthropicModelClient,
        tools_factory=lambda ws, iface, pol, approved: default_registry(
            ws, interface=iface, policy=pol, approved_tools=approved
        ),
        config_factory=lambda iface, pol, approved: default_config(
            interface=iface, policy=pol, approved_tools=approved
        ),
        run_store=run_store,
        classify_fn=classify_query,
        patch_store=patch_store,
        faithfulness_fn=check_faithfulness,
    )


def set_runtime(runtime: DurableRuntime) -> None:
    """Override the process-local runtime (used by main.py and tests)."""
    global _runtime
    _runtime = runtime


def reset_runtime() -> None:
    global _runtime
    _runtime = None


def get_runtime() -> DurableRuntime:
    global _runtime
    if _runtime is None:
        _runtime = default_runtime()
    return _runtime


def dbos_database_url(url: str | None = None) -> str:
    """Resolve and normalize the DBOS system database URL.

    Uses ``DBOS_SYSTEM_DATABASE_URL``, falling back to ``DATABASE_URL`` on
    Railway, or a local SQLite file when neither is configured.
    """
    raw = (
        url
        or os.environ.get("DBOS_SYSTEM_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or _DEFAULT_DBOS_SQLITE_URL
    )
    if raw.startswith("postgres://"):
        return "postgresql://" + raw[len("postgres://"):]
    if raw.startswith("postgresql+psycopg://"):
        return "postgresql://" + raw[len("postgresql+psycopg://"):]
    return raw


def is_dbos_launched() -> bool:
    return _launched


def launch_dbos(
    system_database_url: str | None = None,
    *,
    app_name: str = "pd-chatbot",
    # 2.0.0: the workflow became AgentLoop over steps (TASK-5.31), so its step
    # sequence changed; DBOS only recovers workflows of the running version.
    application_version: str = "2.0.0",
    executor_id: str | None = None,
    log_level: str = "WARNING",
) -> None:
    """Configure and launch the DBOS runtime (idempotent if already launched)."""
    global _launched
    if _launched:
        return
    url = dbos_database_url(system_database_url)
    if url.startswith("sqlite") and os.environ.get("RAILWAY_ENVIRONMENT_NAME"):
        logger.warning(
            "DATABASE_URL / DBOS_SYSTEM_DATABASE_URL not set on Railway; "
            "DBOS checkpoints are stored in a local SQLite file"
        )
    config: DBOSConfig = {
        "name": app_name,
        "application_version": application_version,
        "system_database_url": url,
        "log_level": log_level,
    }
    if executor_id:
        config["executor_id"] = executor_id
    DBOS(config=config)
    DBOS.launch()
    _launched = True


def destroy_dbos(*, workflow_completion_timeout_sec: int = 0) -> None:
    """Shut down the DBOS runtime while keeping registered workflow decorators."""
    global _launched
    if not _launched:
        return
    try:
        DBOS.destroy(
            destroy_registry=False,
            workflow_completion_timeout_sec=workflow_completion_timeout_sec,
        )
    finally:
        _launched = False


def _persist_event(run_id: str, session_id: str, seq: int, event: Any) -> None:
    store = get_runtime().run_store
    if store is None:
        return
    ev_type, payload = event_to_record(event)
    try:
        store.append_event(
            run_id, session_id, seq, ev_type, payload, ignore_existing=True
        )
    except Exception:
        logger.exception("failed to persist durable event seq=%d for %s", seq, run_id)


# --- Checkpointed DBOS Steps -------------------------------------------------


@DBOS.step()
def _step_init_run(
    run_id: str,
    session_id: str,
    question: str,
    history: list[HistoryItem],
    interface: str,
    model_name: str,
) -> dict[str, Any]:
    """Create the RunStore header, run injection/scope checks, and return routing."""
    runtime = get_runtime()
    if runtime.run_store is not None:
        runtime.run_store.start_run(
            run_id=run_id,
            session_id=session_id,
            question=question,
            interface=interface,
            model=model_name,
            ignore_existing=True,
        )

    if check_prompt_injection(question):
        return {
            "early_exit": "refused",
            "query_type": "conceptual",
            "answer": _INJECTION_RESPONSE,
            "scope_note": None,
        }

    if runtime.classify_fn is None:
        return {
            "early_exit": None,
            "query_type": "both",
            "answer": "",
            "scope_note": None,
        }

    classification = runtime.classify_fn(question, history)
    query_type = classification.get("query_type", "both")
    scope_action = scope.decide(classification.get("scope"))
    if scope_action in (scope.REFUSE_OFF_TOPIC, scope.REFUSE_POLICY):
        return {
            "early_exit": "refused",
            "query_type": query_type,
            "answer": scope.refusal_text(classification.get("scope")),
            "scope_note": None,
        }
    scope_note = (
        scope.mixed_scope_note(classification)
        if scope_action == scope.ANSWER_WITH_DECLINE
        else None
    )
    return {
        "early_exit": None,
        "query_type": query_type,
        "answer": "",
        "scope_note": scope_note,
    }


@DBOS.step()
def _step_load_session_patches(session_id: str) -> dict[str, Any]:
    """Checkpoint the session's stored patches so the run edits them, not new ones."""
    store = get_runtime().patch_store
    if store is None:
        return {}
    return store.load_workspace(session_id).to_snapshot()


@DBOS.step()
def _step_record_event(
    run_id: str, session_id: str, seq: int, ev_type: str, payload: dict[str, Any]
) -> None:
    """Persist one run event. Checkpointed, so a replay never writes it twice."""
    store = get_runtime().run_store
    if store is None:
        return
    try:
        store.append_event(
            run_id, session_id, seq, ev_type, payload, ignore_existing=True
        )
    except Exception:
        logger.exception("failed to persist durable event seq=%d for %s", seq, run_id)


@DBOS.step()
def _step_set_run_status(run_id: str, status: str) -> None:
    store = get_runtime().run_store
    if store is not None:
        store.update_run_status(run_id, status)


@DBOS.step()
async def _step_model_turn(
    run_id: str, turn_num: int, request: dict[str, Any]
) -> dict[str, Any] | None:
    """Run one model turn and checkpoint the completed turn (None: no turn came back)."""
    model_request = ModelRequest(**request)
    client = get_runtime().client_factory()
    final: ModelTurn | None = None
    obs = observability.start_obs(
        "agent_turn",
        as_type="generation",
        model=model_request.model,
        metadata={"turn": turn_num, "run_id": run_id, "durable": True},
    )
    try:
        async for piece in client.stream_turn(model_request):
            if not isinstance(piece, ModelDelta):
                final = piece
    finally:
        trace_turn(obs, model_request.model, final)
    if final is None:
        return None
    return {
        "content": list(final.content),
        "text": final.text,
        "tool_calls": [
            {"id": c.id, "name": c.name, "input": dict(c.input)} for c in final.tool_calls
        ],
        "stop_reason": final.stop_reason,
        "usage": dict(final.usage) if final.usage is not None else None,
    }


@DBOS.step()
async def _step_execute_tool(
    tool_name: str,
    arguments: dict[str, Any],
    workspace_snapshot: dict[str, Any],
    interface: str,
    approved_tools: list[str],
) -> dict[str, Any]:
    """Run one tool call under the guardrails and checkpoint its outcome and workspace."""
    runtime = get_runtime()
    policy = policy_for(interface)
    approved_set = frozenset(approved_tools)
    workspace = PatchWorkspace.from_snapshot(workspace_snapshot)
    tools = runtime.tools_factory(workspace, interface, policy, approved_set)
    outcome = await asyncio.to_thread(
        run_guarded_tool, tools, tool_name, arguments, policy=policy, approved=approved_set
    )
    return {
        "ok": outcome.ok,
        "summary": outcome.summary,
        "content": outcome.content,
        "chunks": list(outcome.chunks),
        "data": dict(outcome.data),
        "workspace_snapshot": workspace.to_snapshot(),
    }


@DBOS.step()
def _step_finish_run(
    run_id: str,
    session_id: str,
    start_seq: int,
    question: str,
    history: list[HistoryItem],
    workspace_snapshot: dict[str, Any],
    chunks: list[Chunk],
    *,
    status: str,
    query_type: str,
    answer: str,
    model: str,
    turns: int,
    tool_calls: int,
    total_tokens: int,
    cost_usd: float | None,
    stop_reason: str | None,
    budget_exceeded: str | None,
    refused: bool,
    loaded_skills: dict[str, str] | None = None,
    metrics: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Repair patches, check faithfulness, emit artifacts + Done, and finalize the run.

    The same request-level finishing the streaming path does in ``stream_chat``.
    """
    runtime = get_runtime()
    workspace = PatchWorkspace.from_snapshot(workspace_snapshot)
    for entry in workspace.entries.values():
        if entry.touched:
            entry.patch = repair_patch(entry.patch, chunks)
    if runtime.patch_store is not None:
        runtime.patch_store.save_workspace(session_id, workspace)
    patches = tuple(workspace.patches(answered_only=True))
    audio = tuple(workspace.audio())

    seq = start_seq
    for item in patches:
        _persist_event(run_id, session_id, seq, Artifact(kind="pd_patch", data=item))
        seq += 1
    for item in audio:
        _persist_event(run_id, session_id, seq, Artifact(kind="audio", data=item))
        seq += 1

    # As on the streaming path: the answer is already recorded, so a low score
    # appends a visible disclaimer rather than gating a retry.
    if runtime.faithfulness_fn is not None and status == "completed" and not refused:
        faithfulness = runtime.faithfulness_fn(
            question,
            append_patches(answer, patches),
            list(chunks) + workspace.evidence() if chunks else [],
        )
        if faithfulness is not None and (
            faithfulness.get("score") or 0
        ) <= FAITHFULNESS_RETRY_THRESHOLD:
            logger.info(
                "low faithfulness (durable, score=%s): %s",
                faithfulness.get("score"), faithfulness.get("explanation"),
            )
            answer += LOW_FAITHFULNESS_DISCLAIMER
            _persist_event(run_id, session_id, seq, TextDelta(LOW_FAITHFULNESS_DISCLAIMER))
            seq += 1

    extend_history = bool(chunks) and not budget_exceeded and not refused and status == "completed"
    new_history = (
        list(history) + [
            {"role": "user", "content": question},
            {"role": "assistant", "content": strip_patch_blocks(answer)},
        ]
        if extend_history
        else list(history)
    )[-HISTORY_MAX_OUTGOING:]

    skills = tuple(
        {"name": name, "version": version}
        for name, version in (loaded_skills or {}).items()
    )
    done_event = Done(
        answer=answer,
        new_history=new_history,
        chunks=tuple(chunks),
        query_type=query_type,
        turns=turns,
        tool_calls=tool_calls,
        refused=refused,
        budget_exceeded=budget_exceeded,
        patches=patches,
        skills=skills,
        run_id=run_id,
        session_id=session_id,
    )
    _persist_event(run_id, session_id, seq, done_event)

    if runtime.run_store is not None:
        runtime.run_store.finish_run(
            run_id,
            status=status,
            query_type=query_type,
            answer=answer,
            model=model,
            turns=turns,
            tool_calls=tool_calls,
            total_tokens=total_tokens,
            cost_usd=cost_usd,
            stop_reason=stop_reason,
            budget_exceeded=budget_exceeded,
            refused=refused,
        )

    return {
        "run_id": run_id,
        "session_id": session_id,
        "status": status,
        "query_type": query_type,
        "answer": answer,
        "model": model,
        "turns": turns,
        "tool_calls": tool_calls,
        "total_tokens": total_tokens,
        "cost_usd": cost_usd,
        "stop_reason": stop_reason,
        "budget_exceeded": budget_exceeded,
        "refused": refused,
        **{key: 0 for key in RUN_METRICS},
        **(metrics or {}),
        "patches": list(patches),
        "audio": list(audio),
        "skills": list(skills),
        "history": new_history,
    }


#: The loop's counters a durable run reports next to turns and tool calls.
RUN_METRICS = (
    "retrieval_calls",
    "tool_errors",
    "recovered_errors",
    "unresolved_errors",
    "disallowed_tool_calls",
    "blocked_tool_outputs",
)


# --- The durable agent loop ----------------------------------------------------


class _NoClient:
    """The durable loop's model calls go through ``_step_model_turn`` instead."""

    def stream_turn(self, request: ModelRequest):  # pragma: no cover - never called
        raise RuntimeError("the durable loop runs model turns as DBOS steps")


class DurableAgentLoop(AgentLoop):
    """``AgentLoop`` with its model turns and tool calls run as DBOS steps.

    Everything the loop decides — text handling, budgets, error and recovery
    accounting — is inherited, so a durable run and a streaming run behave the
    same (TASK-5.31). What changes is only *how* a turn and a call happen: each
    is a checkpointed step, a tool call may first pause for a human approval, and
    the patch workspace travels between tool steps as a snapshot.

    Every event the loop yields must go through ``record`` in order: the event
    sequence numbers come from one counter here, which the approval events
    raised inside a tool call share.
    """

    def __init__(
        self,
        tools: ToolRegistry,
        config: AgentConfig,
        *,
        run_id: str,
        session_id: str,
        interface: str,
        policy: ToolPolicy,
        approved_tools: Collection[str],
        pause_for_approval: bool,
        approval_timeout_seconds: float,
        workspace_snapshot: dict[str, Any],
        seq: int = 0,
    ):
        super().__init__(_NoClient(), tools, config)
        self.run_id = run_id
        self.session_id = session_id
        self.interface = interface
        self.policy = policy
        self.approved: set[str] = set(approved_tools)
        self.pause_for_approval = pause_for_approval
        self.approval_timeout_seconds = approval_timeout_seconds
        self.workspace_snapshot = workspace_snapshot
        self.seq = seq

    def record(self, event: Any) -> None:
        ev_type, payload = event_to_record(event)
        _step_record_event(self.run_id, self.session_id, self.seq, ev_type, payload)
        self.seq += 1

    def _visible_tool_specs(self) -> list[dict[str, Any]]:
        # When a call can pause for approval, the model must see the tools that
        # need one, or it could never ask for them.
        if self.pause_for_approval:
            return self.tools.filtered(self.policy.allowed).specs()
        permitted = self.policy.permitted_names(approved=frozenset(self.approved))
        return self.tools.filtered(permitted).specs()

    async def _turn_pieces(
        self,
        request: ModelRequest,
        turn_num: int,
        trace_context: dict[str, str] | None,
    ) -> AsyncIterator[ModelDelta | ModelTurn]:
        data = await _step_model_turn(
            self.run_id,
            turn_num,
            {
                "model": request.model,
                "system": request.system,
                "messages": request.messages,
                "tools": request.tools,
                "tool_choice": request.tool_choice,
                "max_tokens": request.max_tokens,
            },
        )
        if data is None:
            return
        if data["text"]:
            # The step keeps only the completed turn; its text is replayed as one
            # delta so the loop's text handling sees what a stream would give it.
            yield ModelDelta(data["text"])
        yield ModelTurn(
            content=data["content"],
            text=data["text"],
            tool_calls=tuple(
                ToolCallRequest(c["id"], c["name"], c["input"]) for c in data["tool_calls"]
            ),
            stop_reason=data["stop_reason"],
            usage=data["usage"],
        )

    def _needs_approval(self, name: str) -> bool:
        return (
            self.pause_for_approval
            and name in self.policy.allowed
            and name in self.policy.require_approval
            and name not in self.approved
        )

    async def _execute_tool(self, call: ToolCallRequest) -> ToolOutcome:
        arguments = dict(call.input)
        if self._needs_approval(call.name):
            denied = await self._wait_for_approval(call.id, call.name, arguments)
            if denied is not None:
                return denied
        res = await _step_execute_tool(
            call.name, arguments, self.workspace_snapshot, self.interface, sorted(self.approved)
        )
        self.workspace_snapshot = res["workspace_snapshot"]
        return ToolOutcome(
            content=res["content"],
            summary=res["summary"],
            ok=res["ok"],
            chunks=tuple(res["chunks"]),
            data=res["data"],
        )

    async def _wait_for_approval(
        self, call_id: str, tool_name: str, arguments: dict[str, Any]
    ) -> ToolOutcome | None:
        """Pause durably for a human decision; the refusal outcome, or None if approved."""
        _, reason = self.policy.check(tool_name, approved=frozenset(self.approved))
        self.record(ApprovalRequested(
            call_id=call_id,
            tool_name=tool_name,
            arguments=arguments,
            reason=reason,
            interface=self.interface,
        ))
        _step_set_run_status(self.run_id, "waiting_approval")
        await DBOS.set_event_async(
            PENDING_APPROVAL_KEY,
            {
                "call_id": call_id,
                "tool_name": tool_name,
                "arguments": arguments,
                "reason": reason,
                "interface": self.interface,
            },
        )
        decision = await DBOS.recv_async(
            topic=APPROVAL_TOPIC, timeout_seconds=self.approval_timeout_seconds
        )
        await DBOS.set_event_async(PENDING_APPROVAL_KEY, None)

        timed_out = decision is None
        decided = decision if isinstance(decision, dict) else {}
        approved = bool(decided.get("approved"))
        comment = str(decided.get("comment", ""))
        self.record(ApprovalResolved(
            call_id=call_id,
            tool_name=tool_name,
            approved=approved,
            comment=comment,
            timed_out=timed_out,
            approver=str(decided.get("approver", "")),
        ))
        _step_set_run_status(self.run_id, "running")

        if approved:
            self.approved.add(tool_name)
            return None
        if timed_out:
            content = (
                f"Approval for {tool_name!r} timed out. Do not retry it; answer from what "
                "you already have and explain what was not run."
            )
            summary = f"{tool_name} approval timed out"
        else:
            note = f" Reason: {comment}" if comment else ""
            content = (
                f"A human reviewer denied approval to run {tool_name!r}.{note} "
                "Answer without running it."
            )
            summary = f"{tool_name} denied by reviewer"
        return ToolOutcome(
            content=content,
            summary=summary,
            ok=False,
            data={"approval_denied": True, "timed_out": timed_out, "comment": comment},
        )


# --- Durable DBOS Workflow ---------------------------------------------------


@DBOS.workflow()
async def durable_agent_workflow(
    run_id: str,
    session_id: str,
    question: str,
    history: list[HistoryItem] | None = None,
    *,
    interface: str = "web",
    approved_tools: list[str] | None = None,
    pause_for_approval: bool = True,
    approval_timeout_seconds: float = 3600.0,
    max_turns: int | None = None,
    max_tokens_budget: int | None = None,
    max_cost_usd: float | None = None,
    model_override: str | None = None,
) -> dict[str, Any]:
    """One agent run as a DBOS workflow: ``AgentLoop`` with checkpointed steps."""
    history_list: list[HistoryItem] = list(history or [])
    policy = policy_for(interface)
    approved_set = frozenset(approved_tools or [])

    runtime = get_runtime()
    base_config = runtime.config_factory(interface, policy, approved_set)
    config = replace(
        base_config,
        model=model_override or base_config.model,
        max_turns=max_turns if max_turns is not None else base_config.max_turns,
        max_tokens_budget=(
            max_tokens_budget if max_tokens_budget is not None
            else base_config.max_tokens_budget
        ),
        max_cost_usd=max_cost_usd if max_cost_usd is not None else base_config.max_cost_usd,
    )

    init = _step_init_run(
        run_id, session_id, question, history_list, interface, config.model
    )
    query_type: str = init["query_type"]

    if init["early_exit"] == "refused":
        _step_record_event(run_id, session_id, 0, *event_to_record(
            Sources(items=(), query_type=query_type)
        ))
        _step_record_event(run_id, session_id, 1, *event_to_record(TextDelta(init["answer"])))
        return _step_finish_run(
            run_id,
            session_id,
            2,
            question,
            history_list,
            {},
            [],
            status="refused",
            query_type=query_type,
            answer=init["answer"],
            model=config.model,
            turns=0,
            tool_calls=0,
            total_tokens=0,
            cost_usd=None,
            stop_reason="refused",
            budget_exceeded=None,
            refused=True,
        )

    # The registry here only advertises tool schemas; calls run in their own
    # step against the run's workspace snapshot.
    loop = DurableAgentLoop(
        runtime.tools_factory(PatchWorkspace(), interface, policy, approved_set),
        config,
        run_id=run_id,
        session_id=session_id,
        interface=interface,
        policy=policy,
        approved_tools=approved_set,
        pause_for_approval=pause_for_approval,
        approval_timeout_seconds=approval_timeout_seconds,
        workspace_snapshot=_step_load_session_patches(session_id),
    )
    async for event in loop.stream(
        question, history=history_list, scope_note=init["scope_note"]
    ):
        if isinstance(event, Sources):
            event = Sources(items=event.items, query_type=query_type)
        loop.record(event)

    state = loop.state
    return _step_finish_run(
        run_id,
        session_id,
        loop.seq,
        question,
        history_list,
        loop.workspace_snapshot,
        list(state.chunks),
        status="failed" if state.stop_reason == "error" else "completed",
        query_type=query_type,
        answer=state.answer,
        model=config.model,
        turns=state.turns,
        tool_calls=state.tool_calls,
        total_tokens=state.total_tokens,
        cost_usd=round(state.cost_usd, 8) if state.usage else None,
        stop_reason=state.stop_reason,
        budget_exceeded=state.budget_exceeded,
        refused=False,
        loaded_skills=dict(state.loaded_skills),
        metrics={key: getattr(state, key) for key in RUN_METRICS},
    )


# --- Public Orchestration Helpers --------------------------------------------


async def start_durable_run(
    question: str,
    history: list[HistoryItem] | None = None,
    *,
    run_id: str | None = None,
    session_id: str | None = None,
    interface: str = "web",
    approved_tools: Collection[str] = (),
    pause_for_approval: bool = True,
    approval_timeout_seconds: float = 3600.0,
    max_turns: int | None = None,
    max_tokens_budget: int | None = None,
    max_cost_usd: float | None = None,
    model_override: str | None = None,
) -> tuple[str, str, WorkflowHandleAsync[dict[str, Any]]]:
    """Start a durable agent run in DBOS and return ``(run_id, session_id, handle)``.

    ``interface`` and ``approved_tools`` decide which tools need a human approval,
    so only server-side code chooses them (TASK-5.30): the HTTP endpoint always
    passes ``web``, and an adapter such as Slack passes its own.
    """
    rid = run_id or uuid.uuid4().hex
    sid = session_id or uuid.uuid4().hex
    with SetWorkflowID(rid):
        handle = await DBOS.start_workflow_async(
            durable_agent_workflow,
            rid,
            sid,
            question,
            list(history or []),
            interface=interface,
            approved_tools=sorted(approved_tools),
            pause_for_approval=pause_for_approval,
            approval_timeout_seconds=approval_timeout_seconds,
            max_turns=max_turns,
            max_tokens_budget=max_tokens_budget,
            max_cost_usd=max_cost_usd,
            model_override=model_override,
        )
    return rid, sid, handle


async def approve_run(
    run_id: str, *, approved: bool, approver: str, comment: str = ""
) -> None:
    """Deliver a human approval/denial decision to a paused durable run.

    ``approver`` names who decided and is stored with the decision. The caller
    must already have checked that this person may approve: this function is the
    in-process channel (the HTTP endpoint, a future Slack button), not a gate.
    """
    if not approver.strip():
        raise ValueError("an approval decision needs the approver's name")
    await DBOS.send_async(
        destination_id=run_id,
        message={"approved": approved, "comment": comment, "approver": approver.strip()},
        topic=APPROVAL_TOPIC,
    )


async def get_pending_approval(
    run_id: str, *, timeout_seconds: float = 0.0
) -> dict[str, Any] | None:
    """Return the pending approval payload for ``run_id``, or None if not paused."""
    value = await DBOS.get_event_async(
        run_id, PENDING_APPROVAL_KEY, timeout_seconds=timeout_seconds
    )
    return value if isinstance(value, dict) else None


async def get_durable_run_result(run_id: str) -> dict[str, Any]:
    """Retrieve an existing durable workflow by ``run_id`` and await its result."""
    handle = await DBOS.retrieve_workflow_async(run_id)
    return await handle.get_result()
