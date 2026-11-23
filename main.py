# main.py
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
from fastapi.responses import StreamingResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from typing import List, Literal, Optional
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from feedback_store import FeedbackStore, UnknownMessageError
from rag import _response_cache, async_stream_chat, _langfuse

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
    if _langfuse is not None:
        _langfuse.flush()

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
        sources: list[dict] = []
        query_type = ""
        async for event in async_stream_chat(req.message, history_dicts):
            if event["type"] == "meta":
                sources = event.get("sources", [])
                query_type = event.get("query_type", "")
            elif event["type"] == "chunk":
                answer_parts.append(event["text"])
            elif event["type"] == "done":
                try:
                    event["message_id"] = await asyncio.to_thread(
                        feedback_store.save_response,
                        req.message, "".join(answer_parts), sources, query_type,
                    )
                except Exception:
                    # Never fail the chat because storage is down; without an id
                    # the frontend simply hides the feedback buttons.
                    logger.exception("failed to store response for feedback")
                    event["message_id"] = None
            yield f"data: {json.dumps(event)}\n\n"

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

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "cache": {
            "size": len(_response_cache),
            "maxsize": _response_cache.maxsize,
            "ttl_seconds": _response_cache.ttl,
        },
    }
