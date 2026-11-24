# main.py
"""HTTP adapter over the agent core.

The endpoint owns transport concerns only: auth, rate limiting, SSE framing, and
attaching the stored message id to the terminal event. Every decision about how
an answer is produced lives in ``agent/``; the typed event stream from
``agent.chat.stream_chat`` is mapped to the wire format the browser already
consumes (``meta`` / ``chunk`` / ``done``), and the agent-specific events
(``tool_call``, ``tool_result``, ``artifact``) are forwarded as additive frames
that older clients ignore.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request, Depends, HTTPException, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from typing import List, Literal, Optional
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from agent.cache import response_cache as _response_cache
from agent.cache import stats as _cache_stats
from agent.chat import stream_chat
from agent.events import Artifact, Done, Sources, TextDelta, ToolCall, ToolResult
from agent.observability import flush as flush_tracing
from feedback_store import FeedbackStore, UnknownMessageError
from pd_file import to_pd
from pd_schema import PdPatchDownload

logger = logging.getLogger(__name__)

# --- Rate limiter ---
# Behind Vercel's edge all requests arrive from a Vercel datacenter IP, so
# we must read the real client IP from X-Forwarded-For. The header is injected
# by Vercel and cannot be spoofed by direct callers when Railway is not
# publicly reachable (only Vercel's egress should reach it).
def _get_real_ip(request: Request) -> str:
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

limiter = Limiter(key_func=_get_real_ip)
app = FastAPI(title="Pd Documentation Assistant")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.on_event("shutdown")
async def shutdown():
    flush_tracing()

# --- CORS ---
ALLOWED_ORIGIN = os.environ["ALLOWED_ORIGIN"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN],
    allow_methods=["POST", "GET"],
    allow_headers=["Content-Type", "X-API-Key"],
)

# --- API key auth (supports rotation: comma-separated CHAT_API_KEYS) ---
CHAT_API_KEYS: set[str] = set(
    k.strip() for k in os.environ["CHAT_API_KEYS"].split(",") if k.strip()
)
api_key_header = APIKeyHeader(name="X-API-Key")

def require_api_key(key: str = Security(api_key_header)):
    if key not in CHAT_API_KEYS:
        raise HTTPException(status_code=403, detail="Invalid API key")


def _verify_hmac(payload_signature: str, key: str) -> bool:
    """Verify HMAC-SHA256 signature with 60s replay window.
    payload_signature format: "message_id|rating|timestamp|signature_hex"
    """
    try:
        message_id, rating, timestamp_str, signature = payload_signature.rsplit("|", 3)
    except ValueError:
        return False
    try:
        ts = int(timestamp_str)
    except ValueError:
        return False
    if abs(int(time.time()) - ts) > 60:
        return False
    payload = f"{message_id}|{rating}|{timestamp_str}"
    expected = hmac.new(
        key.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


# --- Feedback store (Postgres via DATABASE_URL, SQLite locally) ---
feedback_store = FeedbackStore()

# --- Request / response models ---
class HistoryItem(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)

class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    history: List[HistoryItem] = Field(default=[], max_length=20)

class Source(BaseModel):
    heading_path: str
    url: str
    source: str
    content_type: str
    object_name: Optional[str] = None

class ChatResponse(BaseModel):
    answer: str
    sources: List[Source]
    query_type: str
    history: List[HistoryItem]


class FeedbackRequest(BaseModel):
    message_id: str
    rating: Literal["up", "down"]
    timestamp: str       # unix seconds, for replay protection
    signature: str       # HMAC-SHA256(message_id|rating|timestamp, api_key)
    comment: str = ""


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


# --- Endpoints ---
@app.post("/chat", dependencies=[Depends(require_api_key)])
@limiter.limit("10/minute")
async def chat_endpoint(request: Request, req: ChatRequest):
    history_dicts = [h.model_dump() for h in req.history]

    async def generate():
        # Capture the response as it streams so it can be stored with its own id
        # when it completes. Feedback then refers to this stored row, not to the
        # in-memory response cache (which is keyed by question and expires).
        answer_parts: list[str] = []
        final_answer = ""
        sources: list[dict] = []
        query_type = ""
        async for event in stream_chat(req.message, history_dicts):
            if isinstance(event, Sources):
                # The browser creates the assistant message on `meta`, so this
                # frame must come before the first `chunk` (the core guarantees it).
                sources = list(event.items)
                query_type = event.query_type
                yield _sse({"type": "meta", "sources": sources, "query_type": query_type})
            elif isinstance(event, TextDelta):
                answer_parts.append(event.text)
                yield _sse({"type": "chunk", "text": event.text})
            elif isinstance(event, ToolCall):
                yield _sse(event.to_dict())
            elif isinstance(event, ToolResult):
                yield _sse(event.to_dict())
            elif isinstance(event, Artifact):
                yield _sse(event.to_dict())
            elif isinstance(event, Done):
                final_answer = event.answer
                payload = event.to_dict()
                try:
                    payload["message_id"] = await asyncio.to_thread(
                        feedback_store.save_response,
                        req.message,
                        final_answer or "".join(answer_parts),
                        sources,
                        query_type,
                    )
                except Exception:
                    # Never fail the chat because storage is down; without an id
                    # the frontend simply hides the feedback buttons.
                    logger.exception("failed to store response for feedback")
                    payload["message_id"] = None
                yield _sse(payload)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/feedback", dependencies=[Depends(require_api_key)])
@limiter.limit("30/minute")
async def feedback_endpoint(request: Request, req: FeedbackRequest):
    api_key = request.headers.get("X-API-Key", "")
    if not _verify_hmac(
        f"{req.message_id}|{req.rating}|{req.timestamp}|{req.signature}",
        api_key,
    ):
        raise HTTPException(status_code=403, detail="Invalid signature")

    try:
        await asyncio.to_thread(
            feedback_store.save_rating,
            req.message_id, req.rating, int(req.timestamp), req.comment,
        )
    except UnknownMessageError:
        raise HTTPException(status_code=404, detail="Unknown message")

    return {"status": "ok"}

@app.post("/patch/pd", dependencies=[Depends(require_api_key)])
@limiter.limit("30/minute")
async def patch_file_endpoint(request: Request, patch: PdPatchDownload):
    """Convert a rendered patch diagram to a downloadable .pd file."""
    return Response(
        to_pd(patch),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="patch.pd"'},
    )


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "engine": "agent",
        "cache": {
            "size": _cache_stats()["size"],
            "maxsize": _cache_stats()["maxsize"],
            "ttl_seconds": _cache_stats()["ttl_seconds"],
        },
    }
