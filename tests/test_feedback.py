"""Tests for response storage and the /feedback endpoint (TASK-1)."""

import hashlib
import hmac
import importlib
import json
import os
import sys
import time
import types

import cachetools
import pytest
from fastapi.testclient import TestClient

from feedback_store import FeedbackStore, UnknownMessageError, _normalize_url, metadata

API_KEY = "test-key"


_BACKENDS = ["sqlite"] + (["postgres"] if os.environ.get("TEST_DATABASE_URL") else [])


@pytest.fixture(params=_BACKENDS)
def store(request, tmp_path):
    """SQLite always; also Postgres when TEST_DATABASE_URL points at a scratch database."""
    if request.param == "sqlite":
        yield FeedbackStore(f"sqlite:///{tmp_path / 'fb.db'}")
        return
    s = FeedbackStore(os.environ["TEST_DATABASE_URL"])
    yield s
    metadata.drop_all(s.engine)


class TestFeedbackStore:
    def test_each_response_gets_a_unique_id(self, store):
        a = store.save_response("What is osc~?", "A sine oscillator.", [], "object_reference")
        b = store.save_response("What is osc~?", "A sine oscillator.", [], "object_reference")
        assert a != b

    def test_same_question_rated_by_two_users_keeps_both(self, store):
        a = store.save_response("What is osc~?", "answer 1", [], "object_reference")
        b = store.save_response("what is osc~? ", "answer 2", [], "object_reference")
        assert store.save_rating(a, "up", 1)
        assert store.save_rating(b, "down", 2)
        rows = store.get_feedback()
        assert [(r["message_id"], r["rating"]) for r in rows] == [(a, "up"), (b, "down")]

    def test_rating_carries_full_context(self, store):
        sources = [{"heading_path": "osc~", "url": "u", "source": "s", "content_type": "object"}]
        mid = store.save_response("What is osc~?", "A sine oscillator.", sources, "object_reference")
        store.save_rating(mid, "up", 1, "helpful")
        (row,) = store.get_feedback()
        assert row["question"] == "What is osc~?"
        assert row["answer"] == "A sine oscillator."
        assert row["sources"] == sources
        assert row["query_type"] == "object_reference"
        assert row["comment"] == "helpful"

    def test_replayed_rating_is_ignored(self, store):
        mid = store.save_response("q", "a", [], "")
        assert store.save_rating(mid, "up", 1)
        assert not store.save_rating(mid, "down", 2)
        assert [r["rating"] for r in store.get_feedback()] == ["up"]

    def test_unknown_message_is_rejected(self, store):
        with pytest.raises(UnknownMessageError):
            store.save_rating("nope", "up", 1)

    def test_postgres_urls_use_psycopg_driver(self):
        assert _normalize_url("postgres://u:p@h:5432/db") == "postgresql+psycopg://u:p@h:5432/db"
        assert _normalize_url("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
        assert _normalize_url("sqlite:///x.db") == "sqlite:///x.db"


def _sign(message_id: str, rating: str) -> dict:
    ts = str(int(time.time()))
    sig = hmac.new(API_KEY.encode(), f"{message_id}|{rating}|{ts}".encode(), hashlib.sha256).hexdigest()
    return {"message_id": message_id, "rating": rating, "timestamp": ts, "signature": sig}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGIN", "http://localhost")
    monkeypatch.setenv("CHAT_API_KEYS", API_KEY)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'api.db'}")

    async def fake_stream(question, history):
        yield {"type": "meta", "sources": [{"heading_path": "osc~"}], "query_type": "object_reference"}
        yield {"type": "chunk", "text": "A sine "}
        yield {"type": "chunk", "text": "oscillator."}
        yield {"type": "done", "history": []}

    # Stub out rag so these tests need neither the Chroma index nor API keys.
    fake_rag = types.ModuleType("rag")
    fake_rag._response_cache = cachetools.TTLCache(maxsize=8, ttl=3600)
    fake_rag.async_stream_chat = fake_stream
    fake_rag._langfuse = None
    monkeypatch.setitem(sys.modules, "rag", fake_rag)
    monkeypatch.delitem(sys.modules, "main", raising=False)
    main = importlib.import_module("main")
    yield TestClient(main.app), main
    sys.modules.pop("main", None)


def _ask(test_client, question: str) -> str:
    resp = test_client.post("/chat", json={"message": question}, headers={"X-API-Key": API_KEY})
    events = [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: ")]
    return events[-1]["message_id"]


class TestFeedbackEndpoint:
    def test_two_users_same_question_store_two_rows(self, client):
        test_client, main = client
        a, b = _ask(test_client, "What is osc~?"), _ask(test_client, "What is osc~?")
        assert a != b
        for mid, rating in ((a, "up"), (b, "down")):
            r = test_client.post("/feedback", json=_sign(mid, rating), headers={"X-API-Key": API_KEY})
            assert r.status_code == 200
        rows = main.feedback_store.get_feedback()
        assert [r["rating"] for r in rows] == ["up", "down"]

    def test_rating_after_cache_expiry_keeps_context(self, client):
        test_client, main = client
        mid = _ask(test_client, "What is osc~?")
        main._response_cache.clear()  # simulate TTL expiry / restart of the cache
        r = test_client.post("/feedback", json=_sign(mid, "up"), headers={"X-API-Key": API_KEY})
        assert r.status_code == 200
        (row,) = main.feedback_store.get_feedback()
        assert row["question"] == "What is osc~?"
        assert row["answer"] == "A sine oscillator."
        assert row["sources"] == [{"heading_path": "osc~"}]

    def test_unknown_message_id_returns_404(self, client):
        test_client, _ = client
        r = test_client.post("/feedback", json=_sign("missing", "up"), headers={"X-API-Key": API_KEY})
        assert r.status_code == 404
