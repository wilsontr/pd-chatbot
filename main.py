# main.py
import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Optional
from rag import chat

app = FastAPI(title="Pd Documentation Assistant")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"]
)

class ChatRequest(BaseModel):
    message: str
    history: List[Dict] = []

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
    history: List[Dict]

@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(req: ChatRequest):
    answer, chunks, classification, new_history = await asyncio.to_thread(
        chat, req.message, req.history
    )
    return ChatResponse(
        answer=answer,
        sources=[Source(
            heading_path=c["heading_path"],
            url=c["url"],
            source=c["source"],
            content_type=c["content_type"],
            object_name=c.get("object_name")
        ) for c in chunks],
        query_type=classification["query_type"],
        history=new_history
    )

@app.get("/health")
async def health():
    return {"status": "ok"}
