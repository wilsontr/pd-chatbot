"""Event-sourced run log persisted in Postgres (or SQLite locally).

Every agent run gets a ``runs`` row carrying its metadata and Langfuse trace
link, plus an append-only sequence of ``run_events`` rows keyed by
``(run_id, seq)`` and tagged with ``session_id``. Keeping the full event log
lets runs be replayed step by step (for the timeline UI and debugging) and
reconstructed into model message history (for crash recovery and durable runs).

Postgres is used when ``DATABASE_URL`` is set (production on Railway);
otherwise a local SQLite file is used for development and tests.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    false,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from .events import (
    AgentEvent,
    ApprovalRequested,
    ApprovalResolved,
    Artifact,
    Done,
    ModelTurnEvent,
    Sources,
    TextDelta,
    TextReset,
    ToolCall,
    ToolResult,
)

logger = logging.getLogger(__name__)

_DEFAULT_SQLITE_URL = "sqlite:///runs.db"

metadata = MetaData()

runs = Table(
    "runs",
    metadata,
    Column("run_id", Text, primary_key=True),
    Column("session_id", Text, nullable=False, index=True),
    Column("question", Text, nullable=False),
    Column("interface", Text, nullable=False, server_default="web"),
    Column("status", Text, nullable=False, server_default="running"),
    Column("query_type", Text, nullable=False, server_default=""),
    Column("answer", Text, nullable=False, server_default=""),
    Column("model", Text, nullable=False, server_default=""),
    Column("turns", Integer, nullable=False, server_default="0"),
    Column("tool_calls", Integer, nullable=False, server_default="0"),
    Column("total_tokens", Integer, nullable=False, server_default="0"),
    Column("cost_usd", Float, nullable=True),
    Column("latency_ms", Float, nullable=True),
    Column("stop_reason", Text, nullable=True),
    Column("budget_exceeded", Text, nullable=True),
    Column("refused", Boolean, nullable=False, server_default=false()),
    Column("cached", Boolean, nullable=False, server_default=false()),
    Column("langfuse_trace_id", Text, nullable=True, index=True),
    Column("langfuse_trace_url", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("completed_at", DateTime(timezone=True), nullable=True),
)

run_events = Table(
    "run_events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "run_id",
        Text,
        ForeignKey("runs.run_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("session_id", Text, nullable=False, index=True),
    Column("seq", Integer, nullable=False),
    Column("event_type", Text, nullable=False),
    Column("payload", Text, nullable=False),  # JSON object
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("run_id", "seq", name="uq_run_events_run_seq"),
)


def _normalize_url(url: str) -> str:
    """Rewrite Railway/Heroku postgres:// URLs to use the psycopg 3 driver."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


class UnknownRunError(LookupError):
    """Raised when a run id does not exist in the store."""


def event_to_record(event: AgentEvent) -> tuple[str, dict[str, Any]]:
    """Return ``(event_type, payload_dict)`` for persisting any ``AgentEvent``."""
    return event.type, event.to_log_dict()


def event_from_record(event_type: str, payload: dict[str, Any]) -> AgentEvent:
    """Reconstruct a typed ``AgentEvent`` dataclass from a stored event row."""
    if event_type == ModelTurnEvent.type:
        return ModelTurnEvent(
            turn=int(payload.get("turn", 0)),
            model=str(payload.get("model", "")),
            content=tuple(payload.get("content") or ()),
            text=str(payload.get("text", "")),
            stop_reason=payload.get("stop_reason"),
            usage=dict(payload.get("usage") or {}),
            cost_usd=payload.get("cost_usd"),
            latency_ms=float(payload.get("latency_ms") or 0.0),
        )
    if event_type == TextDelta.type:
        return TextDelta(text=str(payload.get("text", "")))
    if event_type == TextReset.type:
        return TextReset()
    if event_type == ToolCall.type:
        return ToolCall(
            id=str(payload.get("id", "")),
            name=str(payload.get("name", "")),
            arguments=dict(payload.get("arguments") or {}),
        )
    if event_type == ToolResult.type:
        return ToolResult(
            id=str(payload.get("id", "")),
            name=str(payload.get("name", "")),
            ok=bool(payload.get("ok", True)),
            summary=str(payload.get("summary", "")),
            content=str(payload.get("content", "")),
            chunks=tuple(payload.get("chunks") or ()),
            data=dict(payload.get("data") or {}),
            latency_ms=payload.get("latency_ms"),
        )
    if event_type == Sources.type:
        return Sources(
            items=tuple(payload.get("sources") or ()),
            query_type=str(payload.get("query_type", "")),
        )
    if event_type == Artifact.type:
        return Artifact(
            kind=str(payload.get("kind", "")),
            data=dict(payload.get("data") or {}),
        )
    if event_type == ApprovalRequested.type:
        return ApprovalRequested(
            call_id=str(payload.get("call_id", "")),
            tool_name=str(payload.get("tool_name", "")),
            arguments=dict(payload.get("arguments") or {}),
            reason=str(payload.get("reason", "")),
            interface=str(payload.get("interface", "")),
        )
    if event_type == ApprovalResolved.type:
        return ApprovalResolved(
            call_id=str(payload.get("call_id", "")),
            tool_name=str(payload.get("tool_name", "")),
            approved=bool(payload.get("approved", False)),
            comment=str(payload.get("comment", "")),
            timed_out=bool(payload.get("timed_out", False)),
        )
    if event_type == Done.type:
        return Done(
            answer=str(payload.get("answer", "")),
            new_history=list(payload.get("history") or []),
            query_type=str(payload.get("query_type", "")),
            turns=int(payload.get("turns", 0)),
            tool_calls=int(payload.get("tool_calls", 0)),
            refused=bool(payload.get("refused", False)),
            cached=bool(payload.get("cached", False)),
            patch_failed=bool(payload.get("patch_failed", False)),
            budget_exceeded=payload.get("budget_exceeded"),
            patches=tuple(payload.get("patches") or ()),
            run_id=payload.get("run_id"),
            session_id=payload.get("session_id"),
            langfuse_trace_id=payload.get("langfuse_trace_id"),
            langfuse_trace_url=payload.get("langfuse_trace_url"),
        )
    raise ValueError(f"Unknown event type: {event_type!r}")


class RunStore:
    """Append-only Postgres/SQLite store for agent runs and their event logs."""

    def __init__(self, url: str | None = None):
        url = url or os.environ.get("DATABASE_URL") or _DEFAULT_SQLITE_URL
        if url.startswith("sqlite") and os.environ.get("RAILWAY_ENVIRONMENT_NAME"):
            logger.warning(
                "DATABASE_URL is not set on Railway; agent runs are stored in a local "
                "SQLite file and will be lost on the next deploy"
            )
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        self.engine: Engine = create_engine(
            _normalize_url(url), pool_pre_ping=True, connect_args=connect_args
        )
        if self.engine.dialect.name == "sqlite":
            from sqlalchemy import event

            @event.listens_for(self.engine, "connect")
            def _fk_on(dbapi_conn, _record):
                dbapi_conn.execute("PRAGMA foreign_keys=ON")

        metadata.create_all(self.engine)

    def start_run(
        self,
        *,
        run_id: str | None = None,
        session_id: str | None = None,
        question: str,
        interface: str = "web",
        model: str = "",
        langfuse_trace_id: str | None = None,
        langfuse_trace_url: str | None = None,
        created_at: datetime | None = None,
        ignore_existing: bool = False,
    ) -> str:
        """Create a new run header row and return its ``run_id``."""
        run_id = run_id or uuid.uuid4().hex
        session_id = session_id or uuid.uuid4().hex
        try:
            with self.engine.begin() as conn:
                conn.execute(
                    insert(runs).values(
                        run_id=run_id,
                        session_id=session_id,
                        question=question,
                        interface=interface,
                        status="running",
                        model=model or "",
                        langfuse_trace_id=langfuse_trace_id,
                        langfuse_trace_url=langfuse_trace_url,
                        created_at=created_at or _utcnow(),
                    )
                )
        except IntegrityError:
            if not ignore_existing:
                raise
        return run_id

    def update_run_status(self, run_id: str, status: str) -> None:
        """Update a run's live status (e.g. ``waiting_approval`` -> ``running``)."""
        with self.engine.begin() as conn:
            conn.execute(
                update(runs).where(runs.c.run_id == run_id).values(status=status)
            )

    def append_event(
        self,
        run_id: str,
        session_id: str,
        seq: int,
        event_type: str,
        payload: dict[str, Any],
        *,
        created_at: datetime | None = None,
        ignore_existing: bool = False,
    ) -> None:
        """Append a single ordered event to a run."""
        self.append_events(
            [{
                "run_id": run_id,
                "session_id": session_id,
                "seq": seq,
                "event_type": event_type,
                "payload": payload,
                "created_at": created_at or _utcnow(),
            }],
            ignore_existing=ignore_existing,
        )

    def append_events(
        self,
        rows: Sequence[dict[str, Any]],
        *,
        ignore_existing: bool = False,
    ) -> None:
        """Atomically append one or more ordered event rows."""
        if not rows:
            return
        values = [
            {
                "run_id": row["run_id"],
                "session_id": row["session_id"],
                "seq": int(row["seq"]),
                "event_type": row["event_type"],
                "payload": (
                    row["payload"]
                    if isinstance(row["payload"], str)
                    else json.dumps(row["payload"])
                ),
                "created_at": row.get("created_at") or _utcnow(),
            }
            for row in rows
        ]
        try:
            with self.engine.begin() as conn:
                conn.execute(insert(run_events), values)
        except IntegrityError:
            if not ignore_existing:
                raise
            # Insert row-by-row ignoring already-persisted (run_id, seq) entries
            # when a durable step is retried after a crash right before commit.
            for item in values:
                try:
                    with self.engine.begin() as conn:
                        conn.execute(insert(run_events).values(**item))
                except IntegrityError:
                    pass

    def finish_run(
        self,
        run_id: str,
        *,
        status: str = "completed",
        query_type: str = "",
        answer: str = "",
        model: str | None = None,
        turns: int = 0,
        tool_calls: int = 0,
        total_tokens: int = 0,
        cost_usd: float | None = None,
        latency_ms: float | None = None,
        stop_reason: str | None = None,
        budget_exceeded: str | None = None,
        refused: bool = False,
        cached: bool = False,
        langfuse_trace_id: str | None = None,
        langfuse_trace_url: str | None = None,
        completed_at: datetime | None = None,
    ) -> None:
        """Record terminal metadata on an existing run."""
        values: dict[str, Any] = {
            "status": status,
            "query_type": query_type or "",
            "answer": answer or "",
            "turns": turns,
            "tool_calls": tool_calls,
            "total_tokens": total_tokens,
            "cost_usd": cost_usd,
            "latency_ms": latency_ms,
            "stop_reason": stop_reason,
            "budget_exceeded": budget_exceeded,
            "refused": refused,
            "cached": cached,
            "completed_at": completed_at or _utcnow(),
        }
        if model is not None:
            values["model"] = model
        if langfuse_trace_id is not None:
            values["langfuse_trace_id"] = langfuse_trace_id
        if langfuse_trace_url is not None:
            values["langfuse_trace_url"] = langfuse_trace_url
        with self.engine.begin() as conn:
            conn.execute(update(runs).where(runs.c.run_id == run_id).values(**values))

    def get_events(self, run_id: str) -> list[dict[str, Any]]:
        """Return every stored event for ``run_id`` in strict ``seq`` order."""
        stmt = (
            select(
                run_events.c.seq,
                run_events.c.run_id,
                run_events.c.session_id,
                run_events.c.event_type,
                run_events.c.payload,
                run_events.c.created_at,
            )
            .where(run_events.c.run_id == run_id)
            .order_by(run_events.c.seq.asc())
        )
        with self.engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        return [
            {
                "seq": row["seq"],
                "run_id": row["run_id"],
                "session_id": row["session_id"],
                "event_type": row["event_type"],
                "payload": json.loads(row["payload"]),
                "created_at": _iso(row["created_at"]),
            }
            for row in rows
        ]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        """Return a run's metadata and full ordered event history, or None."""
        stmt = select(runs).where(runs.c.run_id == run_id)
        with self.engine.connect() as conn:
            row = conn.execute(stmt).mappings().first()
        if row is None:
            return None
        return {
            **self._format_run(row),
            "events": self.get_events(run_id),
        }

    def list_runs(
        self,
        *,
        session_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List stored runs newest-first, optionally filtered by ``session_id``."""
        stmt = select(runs)
        if session_id is not None:
            stmt = stmt.where(runs.c.session_id == session_id)
        stmt = (
            stmt.order_by(runs.c.created_at.desc(), runs.c.run_id.desc())
            .limit(limit)
            .offset(offset)
        )
        with self.engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        return [self._format_run(row) for row in rows]

    def replay_events(self, run_id: str) -> list[AgentEvent]:
        """Reconstruct the typed ``AgentEvent`` stream of a stored run in order."""
        run = self.get_run(run_id)
        if run is None:
            raise UnknownRunError(run_id)
        return [
            event_from_record(ev["event_type"], ev["payload"])
            for ev in run["events"]
        ]

    def reconstruct_state(self, run_id: str) -> dict[str, Any]:
        """Replay a run's event log to rebuild its messages, answer and artifacts.

        Works for both completed runs and interrupted runs (useful for debugging
        and for resuming from the last completed step).
        """
        run = self.get_run(run_id)
        if run is None:
            raise UnknownRunError(run_id)

        messages: list[dict[str, Any]] = [
            {"role": "user", "content": run["question"]}
        ]
        pending_tool_results: list[dict[str, Any]] = []
        answer_parts: list[str] = []
        sources: list[dict[str, Any]] = []
        chunks: list[dict[str, Any]] = []
        patches: list[dict[str, Any]] = []
        audio: list[dict[str, Any]] = []
        usage: dict[str, int] = {}
        cost_usd = 0.0
        has_cost = False
        turns = 0
        tool_calls = 0
        stop_reason: str | None = None
        query_type = run["query_type"]
        done_event: Done | None = None

        def _flush_tool_results() -> None:
            if pending_tool_results:
                messages.append({"role": "user", "content": list(pending_tool_results)})
                pending_tool_results.clear()

        for record in run["events"]:
            ev = event_from_record(record["event_type"], record["payload"])
            if isinstance(ev, ModelTurnEvent):
                _flush_tool_results()
                messages.append({"role": "assistant", "content": list(ev.content)})
                turns = max(turns, ev.turn)
                stop_reason = ev.stop_reason
                for k, v in ev.usage.items():
                    usage[k] = usage.get(k, 0) + int(v or 0)
                if ev.cost_usd is not None:
                    cost_usd += ev.cost_usd
                    has_cost = True
            elif isinstance(ev, ToolResult):
                tool_calls += 1
                chunks.extend(ev.chunks)
                pending_tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": ev.id,
                    "content": ev.content,
                    **({"is_error": True} if not ev.ok else {}),
                })
            elif isinstance(ev, Sources):
                sources = list(ev.items)
                if ev.query_type:
                    query_type = ev.query_type
            elif isinstance(ev, TextDelta):
                answer_parts.append(ev.text)
            elif isinstance(ev, TextReset):
                answer_parts.clear()
            elif isinstance(ev, Artifact):
                if ev.kind == "pd_patch":
                    patches.append(ev.data)
                elif ev.kind == "audio":
                    audio.append(ev.data)
            elif isinstance(ev, Done):
                done_event = ev
                if ev.query_type:
                    query_type = ev.query_type

        _flush_tool_results()
        replayed_answer = done_event.answer if done_event is not None else "".join(answer_parts)

        return {
            "run_id": run["run_id"],
            "session_id": run["session_id"],
            "status": run["status"],
            "question": run["question"],
            "query_type": query_type,
            "messages": messages,
            "answer": replayed_answer,
            "sources": sources,
            "chunks": chunks,
            "patches": patches,
            "audio": audio,
            "turns": turns,
            "tool_calls": tool_calls,
            "usage": usage,
            "cost_usd": round(cost_usd, 8) if has_cost else None,
            "stop_reason": stop_reason,
            "completed": done_event is not None,
            "langfuse_trace_id": run["langfuse_trace_id"],
            "langfuse_trace_url": run["langfuse_trace_url"],
        }

    @staticmethod
    def _format_run(row: Any) -> dict[str, Any]:
        return {
            "run_id": row["run_id"],
            "session_id": row["session_id"],
            "question": row["question"],
            "interface": row["interface"],
            "status": row["status"],
            "query_type": row["query_type"],
            "answer": row["answer"],
            "model": row["model"],
            "turns": row["turns"],
            "tool_calls": row["tool_calls"],
            "total_tokens": row["total_tokens"],
            "cost_usd": row["cost_usd"],
            "latency_ms": row["latency_ms"],
            "stop_reason": row["stop_reason"],
            "budget_exceeded": row["budget_exceeded"],
            "refused": bool(row["refused"]),
            "cached": bool(row["cached"]),
            "langfuse_trace_id": row["langfuse_trace_id"],
            "langfuse_trace_url": row["langfuse_trace_url"],
            "created_at": _iso(row["created_at"]),
            "completed_at": _iso(row["completed_at"]),
        }


class RunRecorder:
    """Async adapter that writes a run's events to ``RunStore`` without blocking streaming.

    Structural events (``model_turn``, ``tool_call``, ``tool_result``, ``sources``,
    ``text_reset``, ``artifact``, ``done``) are flushed immediately so completed
    steps survive a mid-run crash. Consecutive ``text_delta`` events keep their
    individual ``seq`` numbers and timestamps, and are batch-inserted before the
    next structural event or when the run finishes so streaming never stalls on
    per-token DB round-trips.

    Storage errors are logged and suppressed: persisting a run log must never
    abort a live user stream.
    """

    def __init__(self, store: RunStore | None, run_id: str, session_id: str):
        self.store = store
        self.run_id = run_id
        self.session_id = session_id
        self._seq = 0
        self._pending: list[dict[str, Any]] = []
        self._disabled = store is None

    async def start(
        self,
        question: str,
        *,
        interface: str = "web",
        model: str = "",
        langfuse_trace_id: str | None = None,
        langfuse_trace_url: str | None = None,
    ) -> None:
        if self._disabled or self.store is None:
            return
        try:
            await asyncio.to_thread(
                self.store.start_run,
                run_id=self.run_id,
                session_id=self.session_id,
                question=question,
                interface=interface,
                model=model,
                langfuse_trace_id=langfuse_trace_id,
                langfuse_trace_url=langfuse_trace_url,
            )
        except Exception:
            logger.exception("failed to start run log %s", self.run_id)
            self._disabled = True

    async def record(self, event: AgentEvent) -> None:
        if self._disabled or self.store is None:
            return
        event_type, payload = event_to_record(event)
        row = {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "seq": self._seq,
            "event_type": event_type,
            "payload": payload,
            "created_at": _utcnow(),
        }
        self._seq += 1
        self._pending.append(row)
        if not isinstance(event, TextDelta):
            await self.flush()

    async def flush(self) -> None:
        if self._disabled or self.store is None or not self._pending:
            return
        batch = self._pending
        self._pending = []
        try:
            await asyncio.to_thread(self.store.append_events, batch)
        except Exception:
            logger.exception("failed to persist %d event(s) for run %s", len(batch), self.run_id)
            self._disabled = True

    async def finish(self, **kwargs: Any) -> None:
        if self._disabled or self.store is None:
            return
        await self.flush()
        if self._disabled:
            return
        try:
            await asyncio.to_thread(self.store.finish_run, self.run_id, **kwargs)
        except Exception:
            logger.exception("failed to finish run log %s", self.run_id)
            self._disabled = True
