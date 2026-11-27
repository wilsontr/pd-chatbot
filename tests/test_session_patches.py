"""Tests for per-session patch storage and iterative edits (TASK-5.12)."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import update

from agent import cache, chat
from agent.events import Artifact, Done, ToolResult
from agent.session_tokens import mint_session_id
from agent.llm import ModelDelta, ModelTurn, ToolCallRequest
from agent.sessions import SessionPatchStore, patch_pd_url, session_patches
from agent.tools import ToolRegistry
from agent.tools.patch import (
    AnalyzeAudioTool,
    ListPatchesTool,
    ReadPatchTool,
    WritePatchTool,
    patch_tools,
)
from agent.workspace import PatchWorkspace
from pd_schema import PdPatch

CHUNK = {
    "id": "parent-1",
    "heading_path": "osc~",
    "text": "osc~ is a cosine oscillator.",
    "url": "https://example.test/osc~",
    "source": "iem_reference",
    "content_type": "object_reference",
    "object_name": "osc~",
}

IN_SCOPE = {
    "rewritten_query": "what is osc~",
    "query_type": "object_reference",
    "object_names": ["osc~"],
    "scope": "in_scope",
    "off_topic_part": "",
    "pd_query": "what is osc~",
}


def objects(text: str = "osc~ 440") -> list[dict[str, Any]]:
    return [{"id": "0", "type": "obj", "text": text, "inlets": 2, "outlets": 1}]


def pd_patch(text: str = "osc~ 440") -> PdPatch:
    return PdPatch.model_validate({"objects": objects(text), "connections": []})


@pytest.fixture
def store(tmp_path) -> SessionPatchStore:
    return SessionPatchStore(f"sqlite:///{tmp_path / 'sessions.db'}")


@pytest.fixture(autouse=True)
def clean_cache():
    cache.clear()
    yield
    cache.clear()


class StubClient:
    """A model scripted turn by turn, as in tests/test_agent_chat.py."""

    def __init__(self, turns: list[dict[str, Any]]):
        self._turns = list(turns)

    async def stream_turn(self, request):
        script = self._turns.pop(0)
        for delta in script.get("deltas", []):
            yield ModelDelta(delta)
        content: list[dict[str, Any]] = []
        if script.get("text"):
            content.append({"type": "text", "text": script["text"]})
        calls = []
        for raw in script.get("tool_calls", []):
            content.append({
                "type": "tool_use", "id": raw["id"], "name": raw["name"],
                "input": raw.get("input", {}),
            })
            calls.append(ToolCallRequest(raw["id"], raw["name"], raw.get("input", {})))
        yield ModelTurn(
            content=content,
            text=script.get("text", ""),
            tool_calls=tuple(calls),
            stop_reason="tool_use" if calls else "end_turn",
            usage={"input": 10, "output": 5, "total": 15},
        )


def turn(store: SessionPatchStore, session_id: str, question: str, client: StubClient):
    """Run one request in a session the way main.py does, with patch tools."""
    workspace = PatchWorkspace()
    tools = ToolRegistry(patch_tools(workspace, execution=False))

    async def go():
        return [
            event
            async for event in chat.stream_chat(
                question,
                [],
                client=client,
                tools=tools,
                workspace=workspace,
                session_id=session_id,
                patch_store=store,
            )
        ]

    with patch.object(chat, "classify_query", lambda q, h: IN_SCOPE), \
         patch.object(chat, "check_faithfulness", lambda *a, **k: None):
        return asyncio.run(go())


def write_turn(name: str, text: str = "osc~ 440", call_id: str = "c1") -> dict[str, Any]:
    return {"tool_calls": [{
        "id": call_id,
        "name": "write_patch",
        "input": {"name": name, "objects": objects(text), "connections": []},
    }]}


def answer(text: str) -> dict[str, Any]:
    return {"text": text, "deltas": [text]}


def read_turn(key: str, call_id: str = "c1") -> dict[str, Any]:
    return {"tool_calls": [{
        "id": call_id, "name": "read_patch", "input": {"id": key},
    }]}


def edit_by_id_turn(patch_id: str, text: str, call_id: str = "c2") -> dict[str, Any]:
    return {"tool_calls": [{
        "id": call_id,
        "name": "write_patch",
        "input": {
            "id": patch_id,
            "name": "ignored-name",
            "objects": objects(text),
            "connections": [],
        },
    }]}


def artifacts(events) -> list[dict[str, Any]]:
    return [e.data for e in events if isinstance(e, Artifact) and e.kind == "pd_patch"]


class TestStore:
    def test_save_assigns_an_id_and_keeps_it_across_revisions(self, store):
        first = store.save("s1", "tone", pd_patch("osc~ 440"), revision=1)
        assert first.patch_id.startswith("p")
        assert first.pd_url == patch_pd_url("s1", first.patch_id)

        second = store.save("s1", "tone", pd_patch("osc~ 220"), revision=2)
        assert second.patch_id == first.patch_id
        assert second.revision == 2

        # Looking up by id or by name gives the latest revision.
        by_id = store.get("s1", first.patch_id)
        by_name = store.get("s1", "tone")
        assert by_id is not None and by_name is not None
        assert by_id.patch.objects[0].text == "osc~ 220"
        assert by_name.patch_id == first.patch_id

        listing = store.list_for_session("s1")
        assert [item.to_dict()["name"] for item in listing] == ["tone"]
        assert listing[0].to_dict()["boxes"] == 1
        assert listing[0].to_dict()["revision"] == 2

    def test_a_late_replay_does_not_lower_the_revision(self, store):
        store.save("s1", "tone", pd_patch("osc~ 440"), revision=1)
        store.save("s1", "tone", pd_patch("osc~ 220"), revision=3)
        replayed = store.save("s1", "tone", pd_patch("osc~ 440"), revision=1)
        assert replayed.revision == 3

    def test_sessions_are_isolated(self, store):
        first = store.save("s1", "tone", pd_patch())
        second = store.save("s2", "tone", pd_patch())
        assert first.patch_id != second.patch_id
        assert store.list_for_session("s1")[0].patch_id == first.patch_id
        assert store.list_for_session("s2")[0].patch_id == second.patch_id

    def test_delete(self, store):
        store.save("s1", "tone", pd_patch())
        assert store.delete("s1", "tone")
        assert store.list_for_session("s1") == []
        assert store.delete("s1", "tone") is False

    def test_prune_removes_only_patches_older_than_the_retention_window(self, store):
        old = store.save("s1", "old", pd_patch())
        store.save("s1", "keep", pd_patch())
        with store.engine.begin() as conn:
            conn.execute(
                update(session_patches)
                .where(session_patches.c.patch_id == old.patch_id)
                .values(updated_at=datetime.now(timezone.utc) - timedelta(days=45))
            )
        assert store.prune() == 1
        assert [item.name for item in store.list_for_session("s1")] == ["keep"]

    def test_workspace_seeds_reads_and_saves_back(self, store):
        stored = store.save("s1", "tone", pd_patch("osc~ 440"), revision=1)

        workspace = store.load_workspace("s1")
        entry = workspace.entries["tone"]
        assert (entry.patch_id, entry.revisions, entry.pd_url) == (
            stored.patch_id, 1, stored.pd_url)
        assert entry.touched is False

        workspace.write("tone", pd_patch("osc~ 220"))
        store.save_workspace("s1", workspace)

        reloaded = store.load_workspace("s1").entries["tone"]
        assert reloaded.patch_id == stored.patch_id
        assert reloaded.revisions == 2
        assert reloaded.patch.objects[0].text == "osc~ 220"
        # The stored entry is not "touched" by the next request.
        assert reloaded.touched is False
        assert len(store.list_for_session("s1")) == 1

    def test_seeding_does_not_replace_a_caller_supplied_patch(self, store):
        store.save("s1", "tone", pd_patch("osc~ 440"))
        workspace = PatchWorkspace()
        workspace.write("tone", pd_patch("osc~ 110"))
        store.seed_workspace("s1", workspace)
        assert workspace.entries["tone"].patch.objects[0].text == "osc~ 110"


class TestWorkspaceSnapshot:
    def test_ids_and_touched_survive_a_checkpoint_roundtrip(self, store):
        stored = store.save("s1", "tone", pd_patch())
        workspace = store.load_workspace("s1")
        workspace.write("tone", pd_patch("osc~ 220"))

        restored = PatchWorkspace.from_snapshot(json.loads(json.dumps(workspace.to_snapshot())))
        entry = restored.entries["tone"]
        assert entry.patch_id == stored.patch_id
        assert entry.revisions == 2
        assert entry.touched is True
        assert restored.patches(answered_only=True)[0]["id"] == stored.patch_id


class TestTools:
    def test_list_returns_ids_and_read_loads_by_id_or_name(self, store):
        stored = store.save("s1", "tone", pd_patch())
        workspace = store.load_workspace("s1")

        listing = ListPatchesTool(workspace).run({})
        listed = json.loads(listing.content)["patches"]
        assert listed == [{
            "id": stored.patch_id, "name": "tone", "revision": 1, "boxes": 1,
        }]

        read = ReadPatchTool(workspace).run({"id": stored.patch_id})
        payload = json.loads(read.content)
        assert payload["id"] == stored.patch_id
        assert payload["patch"]["objects"][0]["text"] == "osc~ 440"
        assert ReadPatchTool(workspace).run({"name": "tone"}).ok

    def test_list_is_empty_without_patches(self):
        out = ListPatchesTool(PatchWorkspace()).run({})
        assert out.ok and out.data == {"patches": []}
        assert "No patches" in out.content

    def test_write_by_id_edits_the_stored_patch(self, store):
        stored = store.save("s1", "tone", pd_patch("osc~ 440"))
        workspace = store.load_workspace("s1")

        out = WritePatchTool(workspace).run({
            "id": stored.patch_id,
            "name": "ignored-name",
            "objects": objects("osc~ 220"),
            "connections": [],
        })
        assert out.ok, out.content
        # The stored name is the identity; the id only selects the patch.
        assert out.data["name"] == "tone"
        assert out.data["revision"] == 2
        entry = workspace.entries["tone"]
        assert entry.revisions == 2 and entry.patch_id == stored.patch_id
        assert len(workspace.entries) == 1

    def test_write_with_an_unknown_id_does_not_create_a_patch(self, store):
        workspace = store.load_workspace("s1")
        out = WritePatchTool(workspace).run({
            "id": "p00000000000", "name": "tone", "objects": objects(), "connections": [],
        })
        assert not out.ok and "no patch with id" in out.content
        assert workspace.entries == {}

    def test_read_and_analyze_tools_resolve_a_stored_id(self, store):
        stored = store.save("s1", "tone", pd_patch())
        workspace = store.load_workspace("s1")
        out = ReadPatchTool(workspace).run({"id": stored.patch_id})
        assert out.data["id"] == stored.patch_id
        assert out.data["name"] == "tone"
        # The analyzer found the entry by id, then reported it has not been run.
        analysis = AnalyzeAudioTool(workspace).run({"id": stored.patch_id})
        assert not analysis.ok and "Run 'tone' first" in analysis.content


class TestConversation:
    def test_a_follow_up_reads_and_edits_the_stored_patch(self, store):
        first = turn(store, "s1", "Make a 440 Hz tone", StubClient([
            write_turn("tone"), answer("Here is a tone."),
        ]))
        (first_patch,) = artifacts(first)
        stored = store.list_for_session("s1")[0]
        assert first_patch["id"] == stored.patch_id
        assert first_patch["revision"] == 1
        assert first_patch["pd_url"] == stored.pd_url

        second = turn(store, "s1", "Lower it to 220 Hz", StubClient([
            read_turn(stored.patch_id),
            edit_by_id_turn(stored.patch_id, "osc~ 220"),
            answer("Lowered it."),
        ]))
        read_results = [
            e for e in second if isinstance(e, ToolResult) and e.name == "read_patch"
        ]
        assert read_results and "osc~ 440" in read_results[0].content

        (edited,) = artifacts(second)
        assert edited["id"] == stored.patch_id
        assert edited["revision"] == 2
        assert edited["patch"]["objects"][0]["text"] == "osc~ 220"

        # One patch, not a near-duplicate, and the stored copy is the edit.
        listing = store.list_for_session("s1")
        assert [item.name for item in listing] == ["tone"]
        assert listing[0].revision == 2
        assert listing[0].patch.objects[0].text == "osc~ 220"

    def test_an_answer_that_changes_nothing_does_not_redraw_old_patches(self, store):
        turn(store, "s1", "Make a tone", StubClient([
            write_turn("tone"), answer("Here it is."),
        ]))
        events = turn(store, "s1", "What is osc~?", StubClient([answer("An oscillator.")]))
        assert artifacts(events) == []
        done = next(e for e in events if isinstance(e, Done))
        assert done.patches == ()
        # The patch is still stored for the next follow-up.
        assert [item.name for item in store.list_for_session("s1")] == ["tone"]

    def test_a_patch_can_be_edited_by_name(self, store):
        turn(store, "s1", "Make a tone", StubClient([
            write_turn("tone"), answer("Here it is."),
        ]))
        events = turn(store, "s1", "Make it lower", StubClient([
            {"tool_calls": [{
                "id": "c1", "name": "write_patch",
                "input": {"name": "tone", "objects": objects("osc~ 220"), "connections": []},
            }]},
            answer("Lowered."),
        ]))
        (edited,) = artifacts(events)
        assert edited["revision"] == 2
        assert len(store.list_for_session("s1")) == 1

    def test_a_cached_answer_is_saved_into_the_asking_session(self, store):
        question = "Make a 440 Hz tone"
        first = turn(store, "s1", question, StubClient([
            write_turn("tone"), answer("Here is a tone."),
        ]))
        first_id = artifacts(first)[0]["id"]

        # The same question in a different session hits the process cache.
        second = turn(store, "s2", question, StubClient([]))
        done = next(e for e in second if isinstance(e, Done))
        assert done.cached
        (adopted,) = artifacts(second)
        assert adopted["id"] != first_id
        stored = store.list_for_session("s2")[0]
        assert stored.patch_id == adopted["id"]
        assert stored.name == "tone"
        assert store.list_for_session("s1")[0].patch_id == first_id

    def test_a_cached_answer_does_not_clobber_a_newer_session_revision(self, store):
        question = "Make a 440 Hz tone"
        turn(store, "s1", question, StubClient([
            write_turn("tone"), answer("Here is a tone."),
        ]))
        turn(store, "s1", "Lower it", StubClient([
            {"tool_calls": [{
                "id": "c1", "name": "write_patch",
                "input": {"name": "tone", "objects": objects("osc~ 220"), "connections": []},
            }]},
            answer("Lowered."),
        ]))
        # Re-asking the first question replays the cache, but the edit stays.
        replay = turn(store, "s1", question, StubClient([]))
        done = next(e for e in replay if isinstance(e, Done))
        assert done.cached
        stored = store.list_for_session("s1")[0]
        assert stored.revision == 2
        assert stored.patch.objects[0].text == "osc~ 220"
        assert artifacts(replay)[0]["revision"] == 2


class TestApi:
    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CHAT_API_KEYS", "k")
        monkeypatch.setenv("ALLOWED_ORIGIN", "http://localhost")
        from fastapi.testclient import TestClient

        import main

        monkeypatch.setattr(main, "CHAT_API_KEYS", {"k"})
        monkeypatch.setattr(
            main, "patch_store", SessionPatchStore(f"sqlite:///{tmp_path / 'api.db'}")
        )
        main.limiter.reset()
        return TestClient(main.app), main.patch_store

    def test_lists_session_patches_and_downloads_the_latest_pd(self, client):
        http, store = client
        s1, other = mint_session_id(), mint_session_id()
        stored = store.save(s1, "tone", pd_patch("osc~ 440"), revision=1)
        store.save(s1, "tone", pd_patch("osc~ 220"), revision=2)

        assert http.get(f"/sessions/{s1}/patches").status_code in (401, 403)
        listed = http.get(f"/sessions/{s1}/patches", headers={"X-API-Key": "k"})
        assert listed.status_code == 200
        payload = listed.json()
        assert payload["session_id"] == s1
        assert payload["patches"] == [{
            "id": stored.patch_id,
            "name": "tone",
            "revision": 2,
            "boxes": 1,
            "updated_at": payload["patches"][0]["updated_at"],
            "pd_url": stored.pd_url,
        }]

        download = http.get(stored.pd_url, headers={"X-API-Key": "k"})
        assert download.status_code == 200
        assert "osc~ 220" in download.text
        assert 'filename="tone.pd"' in download.headers["content-disposition"]

        assert http.get(
            f"/sessions/{s1}/patches/p_missing.pd", headers={"X-API-Key": "k"}
        ).status_code == 404
        assert http.get(
            f"/sessions/{other}/patches", headers={"X-API-Key": "k"}
        ).json()["patches"] == []

    @pytest.mark.parametrize("forged", ["s1", "0" * 32 + "." + "0" * 64])
    def test_an_id_the_server_did_not_issue_opens_nothing(self, client, forged):
        """TASK-5.29: a stored session is reachable only through the id it was issued."""
        http, store = client
        stored = store.save(forged, "tone", pd_patch("osc~ 440"), revision=1)
        headers = {"X-API-Key": "k"}
        assert http.get(f"/sessions/{forged}/patches", headers=headers).status_code == 404
        assert http.get(stored.pd_url, headers=headers).status_code == 404

    def test_one_sessions_id_does_not_open_another_sessions_patch(self, client):
        http, store = client
        mine, theirs = mint_session_id(), mint_session_id()
        stored = store.save(theirs, "tone", pd_patch("osc~ 440"), revision=1)
        headers = {"X-API-Key": "k"}
        assert http.get(f"/sessions/{mine}/patches", headers=headers).json()["patches"] == []
        assert http.get(
            f"/sessions/{mine}/patches/{stored.patch_id}.pd", headers=headers
        ).status_code == 404
