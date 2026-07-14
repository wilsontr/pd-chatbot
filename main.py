# main.py
import hashlib
import hmac
import json
import os
import sqlite3
import time
import threading
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

from rag import _response_cache, async_stream_chat, _langfuse, _cache_lock, _chunks_to_sources

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


# --- SQLite feedback store ---
_feedback_conn = sqlite3.connect("feedback.db", check_same_thread=False)
_feedback_conn.execute("PRAGMA journal_mode=WAL")
_feedback_conn.execute("PRAGMA busy_timeout=5000")
_feedback_lock = threading.Lock()
with _feedback_lock:
    _feedback_conn.execute("""
        CREATE TABLE IF NOT EXISTS feedback (
            message_id TEXT PRIMARY KEY,
            question   TEXT NOT NULL,
            answer     TEXT,
            chunks     TEXT,
            query_type TEXT,
            rating     TEXT NOT NULL CHECK (rating IN ('up', 'down')),
            comment    TEXT DEFAULT '',
            timestamp  INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    _feedback_conn.commit()

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
        async for event in async_stream_chat(req.message, history_dicts):
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

    question = answer = chunks_json = query_type = ""
    with _cache_lock:
        entry = _response_cache.get(req.message_id)
    if entry is not None:
        question, answer, chunks, classification, _ = entry
        chunks_json = json.dumps(_chunks_to_sources(chunks))
        query_type = classification.get("query_type", "")

    with _feedback_lock:
        _feedback_conn.execute(
            "INSERT OR IGNORE INTO feedback (message_id, question, answer, chunks, query_type, rating, comment, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (req.message_id, question, answer, chunks_json, query_type, req.rating, req.comment, int(req.timestamp)),
        )
        _feedback_conn.commit()

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
