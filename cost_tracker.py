"""Per-request LLM cost accounting.

A chat request fans out to several model calls — history compression, query
classification, generation, and the inline faithfulness guard — and the cost of a
request is the sum of them, not just the visible answer. This module accumulates
those calls for the duration of a request through a context variable, so call
sites only need ``record(...)`` and do not have to thread a tracker through
every function signature.

The context variable also keeps concurrent requests separate: FastAPI runs each
request in its own task, and ``ContextVar`` values are task-local. Work handed to
``asyncio.to_thread`` runs with a copy of the context, but the tracker object is
shared by reference, so records from a thread still land in the request's tracker.

Usage:
    token = cost_tracker.start()
    try:
        ...
        cost_tracker.record("generate", model, usage)
    finally:
        summary = cost_tracker.finish(token)
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
from typing import Any, Mapping

import pricing

_current: contextvars.ContextVar["RequestCost | None"] = contextvars.ContextVar(
    "request_cost", default=None
)


@dataclass
class RequestCost:
    """Accumulated model calls for one request."""

    calls: list[dict[str, Any]] = field(default_factory=list)

    def record(self, kind: str, model: str, usage: Mapping[str, int] | None) -> None:
        if not usage:
            return
        self.calls.append({
            "kind": kind,
            "model": model,
            "usage": dict(usage),
            "cost_usd": pricing.cost_usd(model, usage),
            "cache_savings_usd": pricing.cache_savings_usd(model, usage),
        })

    @property
    def total_usd(self) -> float:
        return sum(c["cost_usd"] for c in self.calls if c["cost_usd"] is not None)

    @property
    def cache_savings_usd(self) -> float:
        return sum(
            c["cache_savings_usd"] for c in self.calls
            if c["cache_savings_usd"] is not None
        )

    def by_kind(self) -> dict[str, dict[str, Any]]:
        """Cost and call count grouped by call kind (classify, generate, ...)."""
        grouped: dict[str, dict[str, Any]] = {}
        for call in self.calls:
            bucket = grouped.setdefault(
                call["kind"], {"calls": 0, "cost_usd": 0.0, "cache_savings_usd": 0.0}
            )
            bucket["calls"] += 1
            bucket["cost_usd"] += call["cost_usd"] or 0.0
            bucket["cache_savings_usd"] += call["cache_savings_usd"] or 0.0
        for bucket in grouped.values():
            bucket["cost_usd"] = round(bucket["cost_usd"], 6)
            bucket["cache_savings_usd"] = round(bucket["cache_savings_usd"], 6)
        return grouped

    def summary(self) -> dict[str, Any]:
        return {
            "calls": len(self.calls),
            "total_usd": round(self.total_usd, 6),
            "cache_savings_usd": round(self.cache_savings_usd, 6),
            "by_kind": self.by_kind(),
            "priced_models": sorted({
                c["model"] for c in self.calls if c["cost_usd"] is not None
            }),
            "unpriced_models": sorted({
                c["model"] for c in self.calls if c["cost_usd"] is None
            }),
        }


def start() -> contextvars.Token:
    """Begin tracking the current request's model costs."""
    return _current.set(RequestCost())


def current() -> RequestCost | None:
    return _current.get()


def record(kind: str, model: str, usage: Mapping[str, int] | None) -> None:
    """Add one model call to the current request (no-op outside a request)."""
    tracker = _current.get()
    if tracker is None:
        return
    tracker.record(kind, model, usage)


def finish(token: contextvars.Token | None = None) -> dict[str, Any] | None:
    """End tracking and return the request summary (None if nothing was tracked)."""
    tracker = _current.get()
    if token is not None:
        try:
            _current.reset(token)
        except ValueError:
            # Token created in a different context (e.g. an async generator's
            # caller); clearing explicitly keeps a stale tracker from leaking.
            _current.set(None)
    else:
        _current.set(None)
    if tracker is None:
        return None
    return tracker.summary()
