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
from agent.session_tokens import is_valid_session_id, mint_session_id
from agent.llm import ModelDelta, ModelTurn, ToolCallRequest
from agent.loop import AgentConfig
from agent.sessions import SessionPatchStore
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
            approver="alice",
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
            await approve_run(
                run_id, approved=True, approver="reviewer-1", comment="approved by reviewer"
            )
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
        assert resolved["payload"]["approver"] == "reviewer-1"

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
            await approve_run(
                run_id, approved=False, approver="reviewer-2", comment="skip sandbox"
            )
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
        assert resolved["payload"]["approver"] == "reviewer-2"

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
        assert resolved["payload"]["approver"] == ""


class TestDurableHttpEndpoints:
    """TASK-5.30: the request cannot choose its tool policy, and only an operator approves."""

    @pytest.fixture
    def api(self, tmp_path, monkeypatch):
        destroy_dbos()
        reset_runtime()
        monkeypatch.setenv("ALLOWED_ORIGIN", "http://localhost")
        monkeypatch.setenv("CHAT_API_KEYS", API_KEY)
        monkeypatch.setenv("ADMIN_API_KEYS", "alice:ops-key")
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'api_runs.db'}")
        monkeypatch.setenv("DBOS_SYSTEM_DATABASE_URL", f"sqlite:///{tmp_path / 'api_dbos.db'}")

        fake_chat = types.ModuleType("agent.chat")
        fake_chat.stream_chat = None  # not used by durable endpoints
        monkeypatch.setitem(sys.modules, "agent.chat", fake_chat)
        monkeypatch.delitem(sys.modules, "main", raising=False)
        main = importlib.import_module("main")

        tool_log: list[str] = []
        seen_interfaces: list[str] = []
        turns = [
            {"tool_calls": [{"id": "c1", "name": "run_patch", "input": {"name": "bell"}}]},
            {"text": "Approved via HTTP API."},
        ]
        client = IndexedScriptedClient(turns)

        def tools_factory(ws, iface, pol, approved):
            seen_interfaces.append(iface)
            return ToolRegistry([
                CountingTool(
                    "run_patch",
                    ToolOutcome(
                        content='{"ran_cleanly": true}',
                        summary="ran bell: clean",
                        data={"name": "bell", "ran_cleanly": True},
                    ),
                    tool_log,
                ),
            ])

        try:
            set_runtime(
                DurableRuntime(
                    client_factory=lambda: client,
                    tools_factory=tools_factory,
                    config_factory=lambda iface, pol, approved: AgentConfig(max_turns=5),
                    run_store=main.run_store,
                    classify_fn=lambda q, h: IN_SCOPE,
                )
            )
            launch_dbos(f"sqlite:///{tmp_path / 'api_dbos.db'}", app_name="pd-api-test")
            with TestClient(main.app) as http:
                yield types.SimpleNamespace(
                    http=http, main=main, tool_log=tool_log, interfaces=seen_interfaces
                )
        finally:
            destroy_dbos()
            reset_runtime()
            sys.modules.pop("main", None)

    @staticmethod
    def _wait_for(http, run_id, status, headers):
        deadline = time.monotonic() + 5.0
        body = None
        while time.monotonic() < deadline:
            r = http.get(f"/runs/{run_id}", headers=headers)
            if r.status_code == 404:  # the workflow has not stored the run yet
                time.sleep(0.05)
                continue
            assert r.status_code == 200
            body = r.json()
            if body["status"] == status:
                return body
            time.sleep(0.05)
        raise AssertionError(f"run never reached {status}: {body and body['status']}")

    @pytest.mark.parametrize("forged", [
        {"interface": "slack"},
        {"interface": "eval"},
        {"approved_tools": ["run_patch"]},
        {"pause_for_approval": False},
    ], ids=["interface-slack", "interface-eval", "approved-tools", "pause-flag"])
    def test_a_request_cannot_choose_its_tool_policy(self, api, forged):
        resp = api.http.post(
            "/runs/durable",
            json={"message": "Run my bell patch", **forged},
            headers={"X-API-Key": API_KEY},
        )
        assert resp.status_code == 422
        assert api.main.run_store.list_runs() == []

    def test_an_http_run_always_uses_the_web_interface(self, api):
        start = api.http.post(
            "/runs/durable", json={"message": "Run my bell patch"},
            headers={"X-API-Key": API_KEY},
        )
        assert start.status_code == 202
        session_id = start.json()["session_id"]
        assert is_valid_session_id(session_id)
        body = self._wait_for(
            api.http, start.json()["run_id"], "completed",
            {"X-API-Key": API_KEY, "X-Admin-Key": "ops-key"},
        )
        assert body["interface"] == "web"
        assert set(api.interfaces) == {"web"}

    def test_only_an_operator_can_approve_and_the_decision_names_them(self, api):
        async def start():
            return await start_durable_run(
                "Run my bell patch", interface="slack", session_id=mint_session_id()
            )

        # On the app's own event loop, as an in-process adapter (Slack) would.
        run_id, session_id, _ = api.http.portal.call(start)
        ops = {"X-API-Key": API_KEY, "X-Admin-Key": "ops-key"}
        paused = self._wait_for(api.http, run_id, "waiting_approval", ops)
        assert paused["pending_approval"]["tool_name"] == "run_patch"

        url = f"/runs/{run_id}/approve"
        decision = {"approved": True, "comment": "go ahead"}
        for headers, params in [
            ({"X-API-Key": API_KEY}, {}),                                    # browser key only
            ({"X-API-Key": API_KEY}, {"session_id": session_id}),            # the requester
            ({"X-API-Key": API_KEY, "X-Admin-Key": "guess"}, {}),             # wrong operator key
        ]:
            r = api.http.post(url, json=decision, headers=headers, params=params)
            assert r.status_code == 403
        assert api.tool_log == []
        assert self._wait_for(api.http, run_id, "waiting_approval", ops)

        approved = api.http.post(url, json=decision, headers=ops)
        assert approved.status_code == 200
        assert approved.json()["approver"] == "alice"

        done = self._wait_for(api.http, run_id, "completed", ops)
        assert done["answer"] == "Approved via HTTP API."
        assert api.tool_log == ["run_patch"]
        resolved = next(e for e in done["events"] if e["event_type"] == "approval_resolved")
        assert resolved["payload"]["approver"] == "alice"
        assert resolved["payload"]["comment"] == "go ahead"

        # Unknown run returns 404 to an operator; bad API key returns 403.
        assert api.http.post(
            "/runs/missing/approve", json={"approved": True}, headers=ops
        ).status_code == 404
        assert api.http.post(
            "/runs/durable", json={"message": "hi"}, headers={"X-API-Key": "wrong"}
        ).status_code == 403

    def test_approve_run_refuses_a_decision_without_an_approver(self):
        with pytest.raises(ValueError):
            asyncio.run(approve_run("any-run", approved=True, approver="  "))


class TestSessionPatchWorkspace:
    def test_a_durable_follow_up_edits_the_stored_patch(self, dbos_env):
        """Two durable runs in one session: the second edits the stored patch."""
        run_store, _ = dbos_env
        patch_store = SessionPatchStore(engine=run_store.engine)
        seen: list[str | None] = []

        class SeededReadTool:
            spec = ToolSpec(
                name="read_patch", description="read", input_schema={"type": "object"}
            )

            def __init__(self, workspace):
                self.workspace = workspace

            def run(self, arguments):
                entry = self.workspace.find(str(arguments.get("name") or ""))
                seen.append(None if entry is None else entry.patch_id)
                content = (
                    json.dumps({"patch": entry.patch.model_dump()})
                    if entry is not None else "no patch"
                )
                return ToolOutcome(content=content, summary="read bell")

        def runtime_with(client):
            return DurableRuntime(
                client_factory=lambda: client,
                tools_factory=lambda ws, iface, pol, approved: ToolRegistry([
                    SeededReadTool(ws),
                    WritePatchTool(ws),
                ]),
                config_factory=lambda iface, pol, approved: AgentConfig(max_turns=5),
                run_store=run_store,
                classify_fn=lambda q, h: IN_SCOPE,
                patch_store=patch_store,
            )

        wire = {"srcId": "0", "srcOutlet": 0, "dstId": "1", "dstInlet": 0}
        first_turns = [
            {"tool_calls": [{"id": "c1", "name": "write_patch", "input": {
                "name": "bell", "objects": OSC_OBJECTS, "connections": [wire],
            }}]},
            {"text": "Built the bell."},
        ]
        second_turns = [
            {"tool_calls": [{"id": "c1", "name": "read_patch", "input": {"name": "bell"}}]},
            {"tool_calls": [{"id": "c2", "name": "write_patch", "input": {
                "name": "bell",
                "objects": [
                    {"id": "0", "type": "obj", "text": "osc~ 220",
                     "inlets": 2, "outlets": 1},
                    {"id": "1", "type": "obj", "text": "dac~",
                     "inlets": 2, "outlets": 0},
                ],
                "connections": [wire],
            }}]},
            {"text": "Lowered it."},
        ]

        async def scenario():
            set_runtime(runtime_with(IndexedScriptedClient(first_turns)))
            _, _, handle = await start_durable_run(
                "Build a bell", run_id="run-patch-1", session_id="sess-patch",
                interface="web", pause_for_approval=False,
            )
            first = await handle.get_result()
            stored = patch_store.list_for_session("sess-patch")[0]

            set_runtime(runtime_with(IndexedScriptedClient(second_turns)))
            _, _, handle = await start_durable_run(
                "Lower it", run_id="run-patch-2", session_id="sess-patch",
                interface="web", pause_for_approval=False,
            )
            second = await handle.get_result()
            return first, second, stored

        first, second, stored = asyncio.run(scenario())

        # The second run's tools saw the first run's patch, id and all.
        assert seen == [stored.patch_id]
        assert first["patches"][0]["id"] == stored.patch_id
        assert first["patches"][0]["revision"] == 1
        # The edit replaced that patch instead of adding a second one.
        assert second["patches"][0]["id"] == stored.patch_id
        assert second["patches"][0]["revision"] == 2
        assert second["patches"][0]["pd_url"] == stored.pd_url
        listing = patch_store.list_for_session("sess-patch")
        assert [item.name for item in listing] == ["bell"]
        assert listing[0].revision == 2
        assert listing[0].patch.objects[0].text == "osc~ 220"


class TestSameLoopAsStreaming:
    """TASK-5.31: the durable runner is AgentLoop over steps, not a copy of it."""

    METRICS = (
        "turns", "tool_calls", "total_tokens", "retrieval_calls", "tool_errors",
        "recovered_errors", "unresolved_errors", "disallowed_tool_calls",
        "blocked_tool_outputs", "budget_exceeded", "stop_reason",
    )

    WIRE = {"srcId": "0", "srcOutlet": 0, "dstId": "1", "dstInlet": 0}

    REPAIR = [
        # Narration before a search is dropped.
        {"text": "Let me look that up.",
         "tool_calls": [{"id": "c1", "name": "search_docs", "input": {"query": "osc~"}}]},
        {"tool_calls": [{"id": "c2", "name": "write_patch", "input": {
            "name": "bell", "objects": OSC_OBJECTS, "connections": [WIRE]}}]},
        # The first run fails, the second recovers.
        {"tool_calls": [{"id": "c3", "name": "run_patch", "input": {"name": "bell"}}]},
        {"tool_calls": [{"id": "c4", "name": "run_patch", "input": {"name": "bell"}}]},
        {"text": "It now runs cleanly."},
    ]

    # Text written beside write_patch is kept (the old durable copy lost it).
    KEEP = [
        {"tool_calls": [{"id": "c1", "name": "search_docs", "input": {"query": "osc~"}}]},
        {"text": "Here is a bell built from osc~.",
         "tool_calls": [{"id": "c2", "name": "write_patch", "input": {
             "name": "bell", "objects": OSC_OBJECTS, "connections": [WIRE]}}]},
        {"text": "Play it with the toggle."},
    ]

    BUDGET = [
        {"text": "Checking.",
         "tool_calls": [{"id": "c1", "name": "search_docs", "input": {"query": "osc~"}}],
         "usage": {"input": 5000, "output": 100, "total": 5100}},
        {"text": "never reached"},
    ]

    @staticmethod
    def _registry(workspace, runs: list[int]):
        class FlakyRunPatch:
            spec = ToolSpec(name="run_patch", description="run", input_schema={"type": "object"})

            def run(self, arguments):
                runs.append(1)
                clean = len(runs) > 1
                return ToolOutcome(
                    content=json.dumps({"ran_cleanly": clean}),
                    summary="ran bell",
                    data={"name": arguments.get("name"), "ran_cleanly": clean},
                )

        return ToolRegistry([
            CountingTool(
                "search_docs",
                ToolOutcome(content="osc~ docs", summary="1 hit", chunks=(CHUNK,)),
                [],
            ),
            WritePatchTool(workspace),
            FlakyRunPatch(),
        ])

    def _streaming(self, turns, config):
        from agent.loop import AgentLoop

        loop = AgentLoop(
            IndexedScriptedClient(turns), self._registry(PatchWorkspace(), []), config
        )

        async def drain():
            async for _ in loop.stream("Build a bell"):
                pass

        asyncio.run(drain())
        return loop.state

    def _durable(self, turns, config, run_store, run_id):
        runs: list[int] = []
        client = IndexedScriptedClient(turns)
        set_runtime(DurableRuntime(
            client_factory=lambda: client,
            tools_factory=lambda ws, iface, pol, approved: self._registry(ws, runs),
            config_factory=lambda iface, pol, approved: config,
            run_store=run_store,
            classify_fn=lambda q, h: IN_SCOPE,
        ))

        async def go():
            _, _, handle = await start_durable_run(
                "Build a bell", run_id=run_id, session_id="sess-same",
                interface="web", pause_for_approval=False,
            )
            return await handle.get_result()

        return asyncio.run(go())

    @pytest.mark.parametrize("script,config", [
        ("REPAIR", AgentConfig(max_turns=6)),
        ("KEEP", AgentConfig(max_turns=6)),
        ("BUDGET", AgentConfig(max_turns=6, max_tokens_budget=1000)),
        ("REPAIR", AgentConfig(max_turns=2)),
    ], ids=["repair", "text-beside-write-patch", "budget-stop", "turn-limit"])
    def test_a_durable_run_matches_the_streaming_loop(self, dbos_env, script, config):
        run_store, _ = dbos_env
        turns = getattr(self, script)
        streamed = self._streaming(turns, config)
        durable = self._durable(
            turns, config, run_store, f"run-same-{script}-{config.max_turns}"
        )

        assert durable["answer"] == streamed.answer
        assert {k: durable[k] for k in self.METRICS} == {
            k: getattr(streamed, k) for k in self.METRICS
        }
        assert durable["cost_usd"] == round(streamed.cost_usd, 8)
        assert durable["status"] == "completed"

    def test_text_beside_write_patch_survives_a_durable_run(self, dbos_env):
        run_store, _ = dbos_env
        result = self._durable(self.KEEP, AgentConfig(max_turns=6), run_store, "run-keep")
        assert result["answer"] == "Here is a bell built from osc~.\n\nPlay it with the toggle."

    def test_the_repair_run_counts_the_recovery(self, dbos_env):
        run_store, _ = dbos_env
        result = self._durable(self.REPAIR, AgentConfig(max_turns=6), run_store, "run-rep")
        assert result["answer"] == "It now runs cleanly."
        assert (result["tool_errors"], result["recovered_errors"]) == (1, 1)
        assert result["unresolved_errors"] == 0

        stored = run_store.get_run("run-rep")
        types_ = [ev["event_type"] for ev in stored["events"]]
        seqs = [ev["seq"] for ev in stored["events"]]
        assert seqs == list(range(len(seqs)))
        assert types_.index("sources") < types_.index("text_delta")
        assert types_[-1] == "done"

    def test_a_low_faithfulness_score_appends_the_disclaimer(self, dbos_env):
        from agent.guardrails import LOW_FAITHFULNESS_DISCLAIMER

        run_store, _ = dbos_env
        client = IndexedScriptedClient([
            {"tool_calls": [{"id": "c1", "name": "search_docs", "input": {"query": "osc~"}}]},
            {"text": "osc~ is a sawtooth."},
        ])
        checked: list[str] = []

        def judge(question, answer, chunks):
            checked.append(answer)
            return {"score": 1, "explanation": "contradicts the excerpt"}

        set_runtime(DurableRuntime(
            client_factory=lambda: client,
            tools_factory=lambda ws, iface, pol, approved: self._registry(ws, []),
            config_factory=lambda iface, pol, approved: AgentConfig(max_turns=4),
            run_store=run_store,
            classify_fn=lambda q, h: IN_SCOPE,
            faithfulness_fn=judge,
        ))

        async def go():
            _, _, handle = await start_durable_run(
                "What is osc~?", run_id="run-faith", session_id="sess-faith",
                interface="web", pause_for_approval=False,
            )
            return await handle.get_result()

        result = asyncio.run(go())
        assert checked == ["osc~ is a sawtooth."]
        assert result["answer"] == "osc~ is a sawtooth." + LOW_FAITHFULNESS_DISCLAIMER
        assert run_store.get_run("run-faith")["answer"] == result["answer"]
