# main.py
import json
import os
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

from rag import _response_cache, async_stream_chat

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

# --- CORS ---
ALLOWED_ORIGIN = os.environ["ALLOWED_ORIGIN"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN],
    allow_methods=["POST", "GET"],
    allow_headers=["Content-Type", "X-API-Key"],
)

# --- API key auth ---
CHAT_API_KEY = os.environ["CHAT_API_KEY"]
api_key_header = APIKeyHeader(name="X-API-Key")

def require_api_key(key: str = Security(api_key_header)):
    if key != CHAT_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")

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
