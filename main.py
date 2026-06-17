# main.py
import asyncio
import os
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request, Depends, HTTPException, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from typing import List, Literal, Optional
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from rag import chat, _response_cache

# --- Rate limiter ---
limiter = Limiter(key_func=get_remote_address)
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
@app.post("/chat", response_model=ChatResponse, dependencies=[Depends(require_api_key)])
@limiter.limit("10/minute")
async def chat_endpoint(request: Request, req: ChatRequest):
    history_dicts = [h.model_dump() for h in req.history]
    answer, chunks, classification, new_history = await asyncio.to_thread(
        chat, req.message, history_dicts
    )
    return ChatResponse(
        answer=answer,
        sources=[Source(
            heading_path=c["heading_path"],
            url=c["url"],
            source=c["source"],
            content_type=c["content_type"],
            object_name=c.get("object_name"),
        ) for c in chunks],
        query_type=classification["query_type"],
        history=[HistoryItem(**h) for h in new_history],
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
