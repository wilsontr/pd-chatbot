"""Tracing and cost telemetry for one request.

Langfuse traces and New Relic custom metrics are the two places a request reports
what it did; both are optional (no keys configured means a no-op). Kept out of
the loop so observability failures can never propagate into an answer, and out of
``rag.py`` so the agent core and the pipeline arm report the same way.
"""

from __future__ import annotations

import logging
from typing import Any

import newrelic.agent
from langfuse import get_client as _get_langfuse

import cost_tracker
import pricing

logger = logging.getLogger(__name__)

# ``None`` means tracing is unavailable; every helper below then no-ops.
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


def langfuse() -> Any | None:
    """The shared Langfuse client, or None when tracing is not configured."""
    return _langfuse


def flush() -> None:
    """Flush pending trace events (called on server shutdown)."""
    if _langfuse is None:
        return
    try:
        _langfuse.flush()
    except Exception:
        logger.debug("langfuse flush failed", exc_info=True)


class _NoopObs:
    """Stand-in when Langfuse is unavailable. Supports update/end/__enter__/__exit__."""

    trace_id: str | None = None
    id: str | None = None

    def update(self, **kwargs: Any) -> None: pass
    def end(self) -> None: pass
    def __enter__(self) -> "_NoopObs": return self
    def __exit__(self, *a: Any) -> None: pass


def start_obs(name: str, as_type: str = "span", **kwargs: Any) -> Any:
    if _langfuse is None:
        return _NoopObs()
    if kwargs.get("trace_context") is None:
        kwargs.pop("trace_context", None)
    return _langfuse.start_observation(as_type=as_type, name=name, **kwargs)


def start_obs_ctx(name: str, as_type: str = "span", **kwargs: Any) -> Any:
    """Context-manager observation; sets as current for nested-child propagation."""
    if _langfuse is None:
        return _NoopObs()
    if kwargs.get("trace_context") is None:
        kwargs.pop("trace_context", None)
    return _langfuse.start_as_current_observation(as_type=as_type, name=name, **kwargs)


def trace_id(obs: Any) -> str | None:
    """Return the Langfuse trace id for an observation, or None when tracing is off."""
    tid = getattr(obs, "trace_id", None)
    return str(tid) if tid else None


def trace_url(obs_or_trace_id: Any) -> str | None:
    """Return the Langfuse UI URL for an observation or trace id, or None."""
    tid = (
        obs_or_trace_id
        if isinstance(obs_or_trace_id, str)
        else trace_id(obs_or_trace_id)
    )
    if not tid or _langfuse is None:
        return None
    try:
        return _langfuse.get_trace_url(trace_id=tid)
    except Exception:
        logger.debug("failed to build langfuse trace url", exc_info=True)
        return None


def trace_context(obs: Any) -> dict[str, str] | None:
    """Build a Langfuse TraceContext dict to nest child observations under ``obs``."""
    tid = trace_id(obs)
    if not tid:
        return None
    ctx: dict[str, str] = {"trace_id": tid}
    span_id = getattr(obs, "id", None)
    if span_id:
        ctx["parent_span_id"] = str(span_id)
    return ctx


class SimpleUsage:
    """Minimal ``.usage`` stand-in so cost_details can price a token dict."""

    def __init__(self, tokens: dict[str, int]):
        self.usage = type("Usage", (), {
            "input_tokens": tokens.get("input_tokens", 0),
            "output_tokens": tokens.get("output_tokens", 0),
            "cache_read_input_tokens": tokens.get("cache_read_tokens", 0),
            "cache_creation_input_tokens": tokens.get("cache_write_tokens", 0),
        })()


def cost_from_usage(model: str, usage: dict[str, int] | None) -> dict[str, float] | None:
    """Langfuse cost_details for a token dict (either spelling — see pricing.py)."""
    if not usage:
        return None
    cost = pricing.cost_usd(model, usage)
    return {"total": round(cost, 8)} if cost is not None else None


def token_usage(resp: Any) -> dict[str, int] | None:
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


def cost_details(model: str, resp: Any) -> dict[str, float] | None:
    """Langfuse per-observation cost, from the same pricing table the eval uses."""
    usage = token_usage(resp)
    if not usage:
        return None
    cost = pricing.cost_usd(model, usage)
    return {"total": round(cost, 8)} if cost is not None else None


def record_scope_decision(scope_label: str, *, refused: bool) -> None:
    """Count refusals in New Relic and score the decision in Langfuse."""
    if refused:
        newrelic.agent.record_custom_metric("Custom/Guard/OutOfScope", 1)
        logger.info("scope refusal (%s)", scope_label)
    if _langfuse is not None:
        try:
            _langfuse.score_current_span(name="scope", value=0 if refused else 1)
        except Exception:
            logger.debug("failed to score scope", exc_info=True)


def record_request_cost(root: Any, meta: dict[str, Any], summary: dict[str, Any]) -> None:
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


def record_model_call(kind: str, model: str, usage: dict[str, int] | None) -> None:
    """Account one model call to the request's cost tracker."""
    cost_tracker.record(kind, model, usage)
