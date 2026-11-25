"""Tests for DBOS-backed durable agent runs: crash recovery & human approval (TASK-5.11)."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import subprocess
import sys
import textwrap
import time
import types
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agent.durable import (
    DurableRuntime,
    approve_run,
    dbos_database_url,
    destroy_dbos,
    get_durable_run_result,
    get_pending_approval,
    launch_dbos,
    reset_runtime,
    set_runtime,
    start_durable_run,
)
from agent.events import ApprovalRequested, ApprovalResolved
from agent.llm import ModelDelta, ModelTurn, ToolCallRequest
from agent.loop import AgentConfig
from agent.store import RunStore, event_from_record, event_to_record
from agent.tools import ToolOutcome, ToolRegistry, ToolSpec
from agent.tools.patch import WritePatchTool
from agent.workspace import PatchRun, PatchWorkspace
from pd_schema import PdPatch
from sandbox.runner import SandboxResult

API_KEY = "test-key"

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
    "rewritten_query": "make an osc~ bell",
    "query_type": "object_reference",
    "object_names": ["osc~"],
    "scope": "in_scope",
    "off_topic_part": "",
    "pd_query": "make an osc~ bell",
}

OSC_OBJECTS = [
    {"id": "0", "type": "obj", "text": "osc~ 440", "inlets": 2, "outlets": 1},
    {"id": "1", "type": "obj", "text": "dac~", "inlets": 2, "outlets": 0},
]


class IndexedScriptedClient:
    """Deterministic scripted client keyed by assistant-turn count in request.messages.

    Keying by how many assistant turns are already in ``request.messages`` makes
    replays and restarts completely deterministic without mutable cursor drift.
    """

    def __init__(self, turns: list[dict[str, Any]]):
        self._turns = turns
        self.calls: list[int] = []

    async def stream_turn(self, request):
        turn_idx = sum(1 for m in request.messages if m.get("role") == "assistant")
        self.calls.append(turn_idx)
        script = self._turns[turn_idx]
        text = script.get("text", "")
        if text:
            yield ModelDelta(text)
        content: list[dict[str, Any]] = []
        if text:
            content.append({"type": "text", "text": text})
        calls = []
        for raw in script.get("tool_calls", []):
            content.append({
                "type": "tool_use",
                "id": raw["id"],
                "name": raw["name"],
                "input": raw.get("input", {}),
            })
            calls.append(ToolCallRequest(raw["id"], raw["name"], raw.get("input", {})))
        yield ModelTurn(
            content=content,
            text=text,
            tool_calls=tuple(calls),
            stop_reason="tool_use" if calls else "end_turn",
            usage=script.get("usage", {"input": 100, "output": 30, "total": 130}),
        )


class CountingTool:
    def __init__(self, name: str, outcome: ToolOutcome, log: list[str]):
        self.spec = ToolSpec(name=name, description=name, input_schema={"type": "object"})
        self._outcome = outcome
        self._log = log

    def run(self, arguments):
        self._log.append(self.spec.name)
        return self._outcome


@pytest.fixture
def dbos_env(tmp_path):
    """Fresh SQLite DBOS + RunStore per test, tearing down DBOS cleanly afterwards."""
    destroy_dbos()
    reset_runtime()
    run_store = RunStore(f"sqlite:///{tmp_path / 'runs.db'}")
    dbos_url = f"sqlite:///{tmp_path / 'dbos.db'}"
    launch_dbos(dbos_url, app_name="pd-chatbot-test")
    yield run_store, dbos_url
    destroy_dbos()
    reset_runtime()


class TestHelpersAndSnapshots:
    def test_dbos_database_url_normalizes_postgres_schemes(self):
        assert dbos_database_url("postgres://u:p@h:5432/db") == "postgresql://u:p@h:5432/db"
        assert (
            dbos_database_url("postgresql+psycopg://u:p@h:5432/db")
            == "postgresql://u:p@h:5432/db"
        )
        assert dbos_database_url("sqlite:///local.db") == "sqlite:///local.db"

    def test_patch_workspace_snapshot_roundtrip_preserves_patches_and_wav(self):
        ws = PatchWorkspace()
        patch = PdPatch.model_validate({
            "objects": OSC_OBJECTS,
            "connections": [{"srcId": "0", "srcOutlet": 0, "dstId": "1", "dstInlet": 0}],
        })
        entry = ws.write("bell", patch)
        entry.revisions = 2
        ws.runs = 1
        entry.run = PatchRun(
            result=SandboxResult(
                status="ok",
                exit_code=0,
                signal=0,
                console="rendered cleanly",
                console_truncated=False,
                wav=b"RIFF\x00\x00\x00\x00WAVE",
                seconds_rendered=1.5,
                peak=0.42,
                nonfinite_samples=0,
            ),
            errors=[],
            inputs=[{"id": "0", "value": "bang", "at_ms": 0.0}],
            analysis={"silent": False, "dominant_hz": 440.0, "peak": 0.42},
        )

        # Ensure snapshot is strictly JSON-serializable.
        raw = json.dumps(ws.to_snapshot())
        restored = PatchWorkspace.from_snapshot(json.loads(raw))
        assert restored.runs == 1
        assert "bell" in restored.entries
        r_entry = restored.entries["bell"]
        assert r_entry.revisions == 2
        assert len(r_entry.patch.objects) == 2
        assert r_entry.run is not None
        assert r_entry.run.result.wav == b"RIFF\x00\x00\x00\x00WAVE"
        assert r_entry.run.analysis == {"silent": False, "dominant_hz": 440.0, "peak": 0.42}

    def test_approval_events_roundtrip(self):
        req = ApprovalRequested(
            call_id="c1",
            tool_name="run_patch",
            arguments={"name": "bell"},
            reason="needs approval",
            interface="slack",
        )
        res = ApprovalResolved(
            call_id="c1",
            tool_name="run_patch",
            approved=True,
            comment="lgtm",
            timed_out=False,
        )
        for ev in (req, res):
            et, payload = event_to_record(ev)
            assert event_from_record(et, payload) == ev


class TestCrashRestartRecovery:
    def test_interrupted_run_resumes_after_process_restart_without_repeating_tools(self, tmp_path):
        """Real cross-process crash test:
        - Process 1 runs Turn 1 (search_docs) + Turn 2 (write_patch), then hard-crashes
          via os._exit(42) right before run_patch executes.
        - Process 2 launches DBOS on the same SQLite database, automatically recovers
          the pending workflow, runs ONLY run_patch + Turn 3, and finishes cleanly.
        - Tool execution log proves search_docs and write_patch were NEVER repeated.
        """
        repo_root = Path(__file__).resolve().parent.parent
        dbos_db = tmp_path / "dbos_crash.db"
        runs_db = tmp_path / "runs_crash.db"
        tool_log = tmp_path / "tool_log.jsonl"
        worker_script = tmp_path / "crash_worker.py"

        worker_script.write_text(
            textwrap.dedent(f"""\
                import asyncio
                import json
                import os
                import sys
                from pathlib import Path

                sys.path.insert(0, {str(repo_root)!r})

                from agent.durable import (
                    DurableRuntime,
                    destroy_dbos,
                    get_durable_run_result,
                    launch_dbos,
                    set_runtime,
                    start_durable_run,
                )
                from agent.llm import ModelDelta, ModelTurn, ToolCallRequest
                from agent.loop import AgentConfig
                from agent.store import RunStore
                from agent.tools import ToolOutcome, ToolRegistry, ToolSpec
                from agent.tools.patch import WritePatchTool

                TOOL_LOG = Path({str(tool_log)!r})
                DBOS_URL = {f"sqlite:///{dbos_db}"!r}
                RUNS_URL = {f"sqlite:///{runs_db}"!r}
                MODE = sys.argv[1]  # "crash" or "recover"

                def log_call(entry: dict) -> None:
                    with TOOL_LOG.open("a") as f:
                        f.write(json.dumps(entry) + "\\n")
                        f.flush()

                OSC_OBJECTS = [
                    {{"id": "0", "type": "obj", "text": "osc~ 440", "inlets": 2, "outlets": 1}},
                    {{"id": "1", "type": "obj", "text": "dac~", "inlets": 2, "outlets": 0}},
                ]

                TURNS = [
                    {{
                        "tool_calls": [{{"id": "c1", "name": "search_docs", "input": {{"query": "osc~"}}}}],
                    }},
                    {{
                        "tool_calls": [{{
                            "id": "c2",
                            "name": "write_patch",
                            "input": {{
                                "name": "bell",
                                "objects": OSC_OBJECTS,
                                "connections": [{{"srcId": "0", "srcOutlet": 0, "dstId": "1", "dstInlet": 0}}],
                            }},
                        }}],
                    }},
                    {{
                        "tool_calls": [{{"id": "c3", "name": "run_patch", "input": {{"name": "bell"}}}}],
                    }},
                    {{
                        "text": "Recovered and ran bell cleanly.",
                    }},
                ]

                class ScriptedClient:
                    async def stream_turn(self, request):
                        idx = sum(1 for m in request.messages if m.get("role") == "assistant")
                        log_call({{"kind": "model_turn", "turn": idx + 1, "mode": MODE}})
                        script = TURNS[idx]
                        text = script.get("text", "")
                        if text:
                            yield ModelDelta(text)
                        content = []
                        if text:
                            content.append({{"type": "text", "text": text}})
                        calls = []
                        for raw in script.get("tool_calls", []):
                            content.append({{
                                "type": "tool_use",
                                "id": raw["id"],
                                "name": raw["name"],
                                "input": raw["input"],
                            }})
                            calls.append(ToolCallRequest(raw["id"], raw["name"], raw["input"]))
                        yield ModelTurn(
                            content=content,
                            text=text,
                            tool_calls=tuple(calls),
                            stop_reason="tool_use" if calls else "end_turn",
                            usage={{"input": 50, "output": 20, "total": 70}},
                        )

                class LoggedSearchTool:
                    spec = ToolSpec(name="search_docs", description="search", input_schema={{"type": "object"}})
                    def run(self, arguments):
                        log_call({{"kind": "tool", "name": "search_docs", "mode": MODE}})
                        return ToolOutcome(
                            content="osc~ docs",
                            summary="1 hit",
                            chunks=({{"id": "p1", "heading_path": "osc~", "text": "osc~", "url": "u", "source": "iem_reference", "content_type": "object_reference", "object_name": "osc~"}},),
                        )

                class LoggedWritePatchTool(WritePatchTool):
                    def run(self, arguments):
                        log_call({{"kind": "tool", "name": "write_patch", "mode": MODE}})
                        return super().run(arguments)

                class CrashThenRunPatchTool:
                    spec = ToolSpec(name="run_patch", description="run", input_schema={{"type": "object"}})
                    def __init__(self, workspace):
                        self.workspace = workspace
                    def run(self, arguments):
                        if MODE == "crash":
                            # Simulate sudden container kill / redeploy before run_patch completes.
                            os._exit(42)
                        assert "bell" in self.workspace.entries, "workspace snapshot must survive restart!"
                        log_call({{"kind": "tool", "name": "run_patch", "mode": MODE}})
                        return ToolOutcome(
                            content='{{"ran_cleanly": true}}',
                            summary="ran bell: clean",
                            data={{"name": "bell", "ran_cleanly": True}},
                        )

                def build_runtime():
                    store = RunStore(RUNS_URL)
                    return DurableRuntime(
                        client_factory=ScriptedClient,
                        tools_factory=lambda ws, iface, pol, approved: ToolRegistry([
                            LoggedSearchTool(),
                            LoggedWritePatchTool(ws),
                            CrashThenRunPatchTool(ws),
                        ]),
                        config_factory=lambda iface, pol, approved: AgentConfig(max_turns=6),
                        run_store=store,
                        classify_fn=lambda q, h: {{"scope": "in_scope", "query_type": "object_reference"}},
                    )

                async def main():
                    set_runtime(build_runtime())
                    launch_dbos(DBOS_URL, app_name="pd-crash-test")
                    try:
                        if MODE == "crash":
                            _, _, handle = await start_durable_run(
                                "Build and run a bell patch",
                                run_id="run-crash-1",
                                session_id="sess-crash-1",
                                interface="web",
                            )
                            await handle.get_result()
                        else:
                            # DBOS.launch() automatically recovers run-crash-1; await its result.
                            result = await get_durable_run_result("run-crash-1")
                            print("RECOVERED_JSON:" + json.dumps(result))
                    finally:
                        destroy_dbos()

                asyncio.run(main())
            """)
        )

        # Process 1: runs steps 1 & 2, then hard-crashes with exit code 42.
        p1 = subprocess.run(
            [sys.executable, str(worker_script), "crash"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert p1.returncode == 42, f"Expected crash exit 42, got {p1.returncode}: {p1.stderr}"

        entries_after_crash = [json.loads(line) for line in tool_log.read_text().splitlines()]
        tools_after_crash = [e["name"] for e in entries_after_crash if e["kind"] == "tool"]
        assert tools_after_crash == ["search_docs", "write_patch"]

        # Process 2: fresh process starts DBOS on same SQLite DB and resumes run-crash-1.
        p2 = subprocess.run(
            [sys.executable, str(worker_script), "recover"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert p2.returncode == 0, f"Recovery process failed: {p2.stderr}\n{p2.stdout}"

        result_line = next(
            line for line in p2.stdout.splitlines() if line.startswith("RECOVERED_JSON:")
        )
        recovered = json.loads(result_line.split("RECOVERED_JSON:", 1)[1])
        assert recovered["status"] == "completed"
        assert recovered["answer"] == "Recovered and ran bell cleanly."
        assert recovered["tool_calls"] == 3
        assert recovered["turns"] == 4
        assert len(recovered["patches"]) == 1
        assert recovered["patches"][0]["name"] == "bell"

        # Verify finished tool calls were NOT repeated after the restart!
        all_entries = [json.loads(line) for line in tool_log.read_text().splitlines()]
        tool_executions = [(e["name"], e["mode"]) for e in all_entries if e["kind"] == "tool"]
        assert tool_executions == [
            ("search_docs", "crash"),
            ("write_patch", "crash"),
            ("run_patch", "recover"),
        ]

        # Verify RunStore recorded the complete, ordered event log without duplicates.
        store = RunStore(f"sqlite:///{runs_db}")
        stored = store.get_run("run-crash-1")
        assert stored is not None
        assert stored["status"] == "completed"
        assert stored["answer"] == "Recovered and ran bell cleanly."
        seqs = [ev["seq"] for ev in stored["events"]]
        assert seqs == sorted(set(seqs))
        assert "artifact" in [ev["event_type"] for ev in stored["events"]]
        assert stored["events"][-1]["event_type"] == "done"


class TestHumanApprovalFlow:
    def test_pause_for_approval_then_continue_once_approved(self, dbos_env):
        run_store, _ = dbos_env
        tool_log: list[str] = []

        turns = [
            {"tool_calls": [{"id": "c1", "name": "search_docs", "input": {"query": "osc~"}}]},
            {"tool_calls": [{"id": "c2", "name": "run_patch", "input": {"name": "bell"}}]},
            {"text": "Approved and ran bell cleanly."},
        ]
        client = IndexedScriptedClient(turns)

        set_runtime(
            DurableRuntime(
                client_factory=lambda: client,
                tools_factory=lambda ws, iface, pol, approved: ToolRegistry([
                    CountingTool(
                        "search_docs",
                        ToolOutcome(content="osc~ docs", summary="1 hit", chunks=(CHUNK,)),
                        tool_log,
                    ),
                    CountingTool(
                        "run_patch",
                        ToolOutcome(
                            content='{"ran_cleanly": true}',
                            summary="ran bell: clean",
                            data={"name": "bell", "ran_cleanly": True},
                        ),
                        tool_log,
                    ),
                ]),
                config_factory=lambda iface, pol, approved: AgentConfig(max_turns=5),
                run_store=run_store,
                classify_fn=lambda q, h: IN_SCOPE,
            )
        )

        async def scenario():
            run_id, _sid, handle = await start_durable_run(
                "Run my bell patch",
                run_id="run-approve-1",
                session_id="sess-slack-1",
                interface="slack",  # Slack policy requires approval for run_patch
                pause_for_approval=True,
                approval_timeout_seconds=10.0,
            )

            # Wait until workflow reaches the approval pause.
            pending = await get_pending_approval(run_id, timeout_seconds=5.0)
            assert pending is not None
            assert pending["tool_name"] == "run_patch"
            assert pending["call_id"] == "c2"
            assert pending["interface"] == "slack"

            # search_docs already ran; run_patch MUST NOT have executed yet.
            assert tool_log == ["search_docs"]
            paused_run = run_store.get_run(run_id)
            assert paused_run is not None
            assert paused_run["status"] == "waiting_approval"

            # Deliver human approval -> workflow resumes and runs run_patch.
            await approve_run(run_id, approved=True, comment="approved by reviewer")
            result = await handle.get_result()
            return result

        result = asyncio.run(scenario())
        assert result["status"] == "completed"
        assert result["answer"] == "Approved and ran bell cleanly."
        assert tool_log == ["search_docs", "run_patch"]

        stored = run_store.get_run("run-approve-1")
        assert stored is not None
        assert stored["status"] == "completed"
        ev_types = [ev["event_type"] for ev in stored["events"]]
        assert "approval_requested" in ev_types
        assert "approval_resolved" in ev_types
        assert ev_types.index("approval_requested") < ev_types.index("approval_resolved")
        resolved = next(ev for ev in stored["events"] if ev["event_type"] == "approval_resolved")
        assert resolved["payload"]["approved"] is True
        assert resolved["payload"]["comment"] == "approved by reviewer"

    def test_denied_approval_skips_tool_and_completes_gracefully(self, dbos_env):
        run_store, _ = dbos_env
        tool_log: list[str] = []

        turns = [
            {"tool_calls": [{"id": "c1", "name": "run_patch", "input": {"name": "bell"}}]},
            {"text": "Understood, I did not run the patch."},
        ]
        client = IndexedScriptedClient(turns)

        set_runtime(
            DurableRuntime(
                client_factory=lambda: client,
                tools_factory=lambda ws, iface, pol, approved: ToolRegistry([
                    CountingTool(
                        "run_patch",
                        ToolOutcome(content="ran", summary="ran"),
                        tool_log,
                    ),
                ]),
                config_factory=lambda iface, pol, approved: AgentConfig(max_turns=5),
                run_store=run_store,
                classify_fn=lambda q, h: IN_SCOPE,
            )
        )

        async def scenario():
            run_id, _sid, handle = await start_durable_run(
                "Run bell",
                run_id="run-deny-1",
                session_id="sess-slack-2",
                interface="slack",
                pause_for_approval=True,
                approval_timeout_seconds=10.0,
            )
            pending = await get_pending_approval(run_id, timeout_seconds=5.0)
            assert pending is not None and pending["tool_name"] == "run_patch"
            await approve_run(run_id, approved=False, comment="skip sandbox")
            return await handle.get_result()

        result = asyncio.run(scenario())
        assert result["status"] == "completed"
        assert result["answer"] == "Understood, I did not run the patch."
        assert tool_log == []  # run_patch was never executed

        stored = run_store.get_run("run-deny-1")
        assert stored is not None
        resolved = next(ev for ev in stored["events"] if ev["event_type"] == "approval_resolved")
        assert resolved["payload"]["approved"] is False
        assert resolved["payload"]["comment"] == "skip sandbox"

    def test_approval_timeout_skips_tool_and_records_timeout(self, dbos_env):
        run_store, _ = dbos_env
        tool_log: list[str] = []

        turns = [
            {"tool_calls": [{"id": "c1", "name": "run_patch", "input": {"name": "bell"}}]},
            {"text": "Approval timed out, answering without running."},
        ]
        client = IndexedScriptedClient(turns)

        set_runtime(
            DurableRuntime(
                client_factory=lambda: client,
                tools_factory=lambda ws, iface, pol, approved: ToolRegistry([
                    CountingTool("run_patch", ToolOutcome(content="ran", summary="ran"), tool_log),
                ]),
                config_factory=lambda iface, pol, approved: AgentConfig(max_turns=5),
                run_store=run_store,
                classify_fn=lambda q, h: IN_SCOPE,
            )
        )

        async def scenario():
            _rid, _sid, handle = await start_durable_run(
                "Run bell",
                run_id="run-timeout-1",
                session_id="sess-slack-3",
                interface="slack",
                pause_for_approval=True,
                approval_timeout_seconds=0.15,
            )
            return await handle.get_result()

        result = asyncio.run(scenario())
        assert result["status"] == "completed"
        assert tool_log == []
        stored = run_store.get_run("run-timeout-1")
        assert stored is not None
        resolved = next(ev for ev in stored["events"] if ev["event_type"] == "approval_resolved")
        assert resolved["payload"]["approved"] is False
        assert resolved["payload"]["timed_out"] is True


class TestDurableHttpEndpoints:
    def test_start_durable_run_and_approve_via_api(self, tmp_path, monkeypatch):
        destroy_dbos()
        reset_runtime()
        monkeypatch.setenv("ALLOWED_ORIGIN", "http://localhost")
        monkeypatch.setenv("CHAT_API_KEYS", API_KEY)
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'api_runs.db'}")
        monkeypatch.setenv("DBOS_SYSTEM_DATABASE_URL", f"sqlite:///{tmp_path / 'api_dbos.db'}")

        fake_chat = types.ModuleType("agent.chat")
        fake_chat.stream_chat = None  # not used by durable endpoints
        monkeypatch.setitem(sys.modules, "agent.chat", fake_chat)
        monkeypatch.delitem(sys.modules, "main", raising=False)
        main = importlib.import_module("main")

        tool_log: list[str] = []
        turns = [
            {"tool_calls": [{"id": "c1", "name": "run_patch", "input": {"name": "bell"}}]},
            {"text": "Approved via HTTP API."},
        ]
        client = IndexedScriptedClient(turns)

        try:
            set_runtime(
                DurableRuntime(
                    client_factory=lambda: client,
                    tools_factory=lambda ws, iface, pol, approved: ToolRegistry([
                        CountingTool(
                            "run_patch",
                            ToolOutcome(
                                content='{"ran_cleanly": true}',
                                summary="ran bell: clean",
                                data={"name": "bell", "ran_cleanly": True},
                            ),
                            tool_log,
                        ),
                    ]),
                    config_factory=lambda iface, pol, approved: AgentConfig(max_turns=5),
                    run_store=main.run_store,
                    classify_fn=lambda q, h: IN_SCOPE,
                )
            )
            launch_dbos(f"sqlite:///{tmp_path / 'api_dbos.db'}", app_name="pd-api-test")

            with TestClient(main.app) as http:
                start_resp = http.post(
                    "/runs/durable",
                    json={
                        "message": "Run my bell patch",
                        "session_id": "sess-api-1",
                        "interface": "slack",
                    },
                    headers={"X-API-Key": API_KEY},
                )
                assert start_resp.status_code == 202
                run_id = start_resp.json()["run_id"]
                assert start_resp.json()["session_id"] == "sess-api-1"

                # Poll GET /runs/{run_id} until waiting_approval.
                deadline = time.monotonic() + 5.0
                run_body = None
                while time.monotonic() < deadline:
                    r = http.get(f"/runs/{run_id}", headers={"X-API-Key": API_KEY})
                    assert r.status_code == 200
                    run_body = r.json()
                    if run_body["status"] == "waiting_approval":
                        break
                    time.sleep(0.05)
                assert run_body is not None and run_body["status"] == "waiting_approval"
                assert run_body["pending_approval"]["tool_name"] == "run_patch"
                assert tool_log == []

                # Approve via HTTP endpoint.
                approve_resp = http.post(
                    f"/runs/{run_id}/approve",
                    json={"approved": True, "comment": "go ahead"},
                    headers={"X-API-Key": API_KEY},
                )
                assert approve_resp.status_code == 200
                assert approve_resp.json()["approved"] is True

                # Poll until completed.
                deadline = time.monotonic() + 5.0
                while time.monotonic() < deadline:
                    r = http.get(f"/runs/{run_id}", headers={"X-API-Key": API_KEY})
                    run_body = r.json()
                    if run_body["status"] == "completed":
                        break
                    time.sleep(0.05)

                assert run_body["status"] == "completed"
                assert run_body["answer"] == "Approved via HTTP API."
                assert tool_log == ["run_patch"]
                ev_types = [e["event_type"] for e in run_body["events"]]
                assert "approval_requested" in ev_types
                assert "approval_resolved" in ev_types
                assert ev_types[-1] == "done"

                # Unknown run returns 404; bad API key returns 403.
                assert (
                    http.post(
                        "/runs/missing/approve",
                        json={"approved": True},
                        headers={"X-API-Key": API_KEY},
                    ).status_code
                    == 404
                )
                assert (
                    http.post(
                        "/runs/durable",
                        json={"message": "hi"},
                        headers={"X-API-Key": "wrong"},
                    ).status_code
                    == 403
                )
        finally:
            destroy_dbos()
            reset_runtime()
            sys.modules.pop("main", None)
