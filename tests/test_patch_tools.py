"""Tests for the patch tools, audio analysis and their artifacts (TASK-5.5)."""

from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
import subprocess
import wave

import numpy as np
import pytest

from agent import artifacts, chat
from agent.audio import analyze
from agent.events import Sources, TextDelta, TextReset
from agent.llm import ModelDelta, ModelTurn, ToolCallRequest
from agent.loop import AgentLoop
from agent.tools import ToolOutcome, ToolRegistry, ToolSpec
from agent.tools.patch import (
    MAX_RUNS,
    AnalyzeAudioTool,
    RunPatchTool,
    WritePatchTool,
    clean_console,
    console_errors,
    parse_inputs,
    runnable_file,
)
from agent.workspace import PatchWorkspace
from pd_schema import PdPatch
from sandbox import SandboxError, SandboxResult, run_patch

RATE = 48000
OBJECTS = [
    {"id": "n", "type": "floatatom", "text": "440", "inlets": 1, "outlets": 1},
    {"id": "o", "type": "obj", "text": "osc~", "inlets": 2, "outlets": 1},
    {"id": "d", "type": "obj", "text": "dac~", "inlets": 2, "outlets": 0},
]
WIRES = [
    {"srcId": "n", "srcOutlet": 0, "dstId": "o", "dstInlet": 0},
    {"srcId": "o", "srcOutlet": 0, "dstId": "d", "dstInlet": 0},
]
ARGS = {"name": "tone", "objects": OBJECTS, "connections": WIRES}


def wav(*channels):
    data = np.stack(channels, axis=1)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(len(channels))
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((np.clip(data, -1, 1) * 32767).round().astype("<i2").tobytes())
    return buf.getvalue()


def sine(hz, amp=0.5, seconds=1.0):
    return amp * np.sin(2 * np.pi * hz * np.arange(int(RATE * seconds)) / RATE)


def result(console="", status="ok", audio=None):
    return SandboxResult(
        status=status, exit_code=0, signal=0, console=console, console_truncated=False,
        wav=audio if audio is not None else wav(sine(440)), seconds_rendered=1.0,
        peak=0.5, nonfinite_samples=0,
    )


@pytest.fixture
def workspace():
    return PatchWorkspace()


def written(workspace, **over):
    out = WritePatchTool(workspace).run({**ARGS, **over})
    assert out.ok, out.content
    return workspace


class TestWrite:
    def test_saves_and_replaces(self, workspace):
        written(workspace)
        RunPatchTool(workspace, lambda *a: result()).run({"name": "tone"})
        assert workspace.entries["tone"].run
        written(workspace)
        entry = workspace.entries["tone"]
        # A new revision has not been run, whatever the old one did.
        assert entry.revisions == 2 and entry.run is None
        assert len(workspace.patches()) == 1

    @pytest.mark.parametrize("over", [
        {"name": "Bad Name"}, {"name": ""}, {"name": None},
        {"objects": []}, {"objects": None}, {"objects": "x"},
        {"objects": OBJECTS * 6},
        {"objects": [OBJECTS[0], OBJECTS[0]], "connections": []},
        {"connections": [{"srcId": "zz", "srcOutlet": 0, "dstId": "o", "dstInlet": 0}]},
        {"objects": [{**OBJECTS[0], "type": "nope"}], "connections": []},
    ])
    def test_invalid_patch_is_explained_not_saved(self, workspace, over):
        out = WritePatchTool(workspace).run({**ARGS, **over})
        assert not out.ok and out.content and not workspace.entries

    def test_schema_matches_the_patch_model(self):
        props = WritePatchTool.spec.input_schema["properties"]
        for field, model in (("objects", "PdObject"), ("connections", "PdConnection")):
            expected = PdPatch.model_json_schema()["$defs"][model]["properties"]
            assert set(props[field]["items"]["properties"]) == set(expected)

    def test_patch_count_is_capped(self, workspace):
        for i in range(4):
            written(workspace, name=f"p{i}")
        assert not WritePatchTool(workspace).run({**ARGS, "name": "p4"}).ok


class TestRun:
    def test_reports_clean_run(self, workspace):
        written(workspace)
        seen = []
        out = RunPatchTool(workspace, lambda text, s: seen.append((text, s)) or result()).run(
            {"name": "tone", "seconds": 1})
        assert out.ok and json.loads(out.content)["ran_cleanly"]
        assert seen[0][1] == 1 and "osc~" in seen[0][0]

    def test_reports_errors(self, workspace):
        written(workspace)
        console = "verbose(0): fm~\nerror: ... couldn't create\n"
        out = RunPatchTool(workspace, lambda *a: result(console)).run({"name": "tone"})
        body = json.loads(out.content)
        assert not body["ran_cleanly"] and body["errors"] == ["error: ... couldn't create"]
        assert "fm~" in body["console"] and "verbose" not in body["console"]

    def test_stopped_run_is_not_clean(self, workspace):
        written(workspace)
        out = RunPatchTool(workspace, lambda *a: result(status="timeout")).run({"name": "tone"})
        assert not json.loads(out.content)["ran_cleanly"] and "timeout" in out.summary

    def test_sandbox_failure_hides_infrastructure_detail(self, workspace):
        written(workspace)

        def broken(*a):
            raise SandboxError("token as-secret rejected by host 10.0.0.1")

        out = RunPatchTool(workspace, broken).run({"name": "tone"})
        assert not out.ok and "secret" not in out.content and "not been run" in out.content
        assert workspace.entries["tone"].run is None

    def test_unknown_patch(self, workspace):
        assert "none yet" in RunPatchTool(workspace).run({"name": "x"}).content

    @pytest.mark.parametrize("args", [
        {"seconds": 11}, {"seconds": -1}, {"seconds": "abc"}, {"seconds": float("nan")},
        {"inputs": "x"}, {"inputs": [1]}, {"inputs": [{"id": "zz", "value": "1"}]},
        {"inputs": [{"id": "n", "value": ""}]},
        {"inputs": [{"id": "n", "value": "1; pd quit"}]},
        {"inputs": [{"id": "n", "value": "1", "at_ms": 99999}]},
        {"inputs": [{"id": "n", "value": "1", "at_ms": float("nan")}]},
        {"inputs": [{"id": "n", "value": "1"}] * 9},
    ])
    def test_bad_arguments_do_not_run(self, workspace, args):
        written(workspace)
        out = RunPatchTool(workspace, lambda *a: pytest.fail("ran")).run({"name": "tone", **args})
        assert not out.ok

    def test_run_limit(self, workspace):
        written(workspace)
        tool = RunPatchTool(workspace, lambda *a: result())
        assert all(tool.run({"name": "tone"}).ok for _ in range(MAX_RUNS))
        assert "limit" in tool.run({"name": "tone"}).content


class TestConsole:
    def test_pd_errors_are_found(self):
        errors = console_errors(
            "error: ... couldn't create\npatch.pd 1 0 2 5 (osc~->dac~) connection failed\n"
            "verbose(3): cannot connect to non-existing object\n")
        assert len(errors) == 3

    def test_printed_output_is_not_an_error(self):
        assert console_errors("print: error 5\nfoo: error\nprint: connection failed\n") == []

    def test_clean_console_only_strips_prefixes(self):
        assert clean_console("verbose(4): a\nb") == "a\nb"


class TestInputs:
    def test_harness_boxes_come_after_the_patch(self):
        patch = PdPatch(objects=OBJECTS, connections=WIRES)
        inputs = parse_inputs([{"id": "n", "value": "220", "at_ms": 50}], patch, 1)
        text = runnable_file(patch, inputs)
        assert "delay 50;" in text and "msg 0 0 220;" in text
        assert "#X connect 3 0 4 0;" in text and "#X connect 5 0 0 0;" in text

    def test_boxes_without_inlets_are_refused(self):
        patch = PdPatch(
            objects=[{"id": "c", "type": "comment", "text": "hi", "inlets": 0, "outlets": 0}],
            connections=[])
        with pytest.raises(ValueError):
            parse_inputs([{"id": "c", "value": "1"}], patch, 1)


class TestAnalysis:
    @pytest.mark.parametrize("hz", [55, 440, 1000.5, 8000])
    def test_dominant_frequency(self, hz):
        a = analyze(wav(sine(hz), sine(hz)))
        assert a["dominant_hz"] == pytest.approx(hz, rel=0.003)
        assert a["rms"] == pytest.approx(0.5 / np.sqrt(2), rel=0.01)
        assert not a["silent"] and a["clipped_fraction"] == 0

    def test_silence(self):
        a = analyze(wav(np.zeros(RATE), np.zeros(RATE)))
        assert a["silent"] and "dominant_hz" not in a

    def test_empty(self):
        assert analyze(wav(np.zeros(0), np.zeros(0)))["silent"]

    def test_clipping(self):
        assert analyze(wav(sine(440, 3.0)))["clipped_fraction"] > 0.5

    def test_opposite_channels_do_not_cancel(self):
        assert analyze(wav(sine(300), -sine(300)))["dominant_hz"] == pytest.approx(300, abs=1)

    def test_several_peaks(self):
        a = analyze(wav(sine(300, 0.4) + sine(900, 0.2)))
        assert [p["hz"] for p in a["spectral_peaks"][:2]] == pytest.approx([300, 900], abs=1)
        assert a["spectral_peaks"][1]["db"] == pytest.approx(-6, abs=0.5)

    def test_segments_show_decay_and_pitch_change(self):
        t = np.arange(RATE) / RATE
        a = analyze(wav(sine(440) * np.exp(-5 * t)))
        levels = [s["rms"] for s in a["segments"]]
        assert levels == sorted(levels, reverse=True)
        b = analyze(wav(np.concatenate([sine(200, 0.5, 0.5), sine(800, 0.5, 0.5)])))
        assert b["segments"][0]["dominant_hz"] == pytest.approx(200, rel=0.03)
        assert b["segments"][-1]["dominant_hz"] == pytest.approx(800, rel=0.03)

    def test_dc_only(self):
        a = analyze(wav(np.full(RATE, 0.5)))
        assert a["dc_offset"] == pytest.approx(0.5, abs=0.001) and a["dominant_hz"] is None

    def test_result_is_json(self):
        json.dumps(analyze(wav(np.random.default_rng(0).normal(0, 0.2, RATE))), allow_nan=False)

    def test_tool_needs_a_run_of_the_current_revision(self, workspace):
        written(workspace)
        tool = AnalyzeAudioTool(workspace)
        assert not tool.run({"name": "tone"}).ok
        RunPatchTool(workspace, lambda *a: result()).run({"name": "tone"})
        assert json.loads(tool.run({"name": "tone"}).content)["dominant_hz"] == pytest.approx(440, 1)
        written(workspace)
        assert not tool.run({"name": "tone"}).ok


class TestArtifacts:
    def test_audio_of_the_current_revision_only(self, workspace):
        written(workspace)
        assert workspace.audio() == []
        RunPatchTool(workspace, lambda *a: result()).run({"name": "tone"})
        (item,) = workspace.audio()
        assert item["url"] == f"/artifacts/{item['id']}" and item["name"] == "tone"
        assert artifacts.get(item["id"]).data[:4] == b"RIFF"
        written(workspace)
        assert workspace.audio() == []

    def test_silence_is_not_an_artifact(self, workspace):
        written(workspace)
        silent = result(audio=wav(np.zeros(RATE), np.zeros(RATE)))
        RunPatchTool(workspace, lambda *a: silent).run({"name": "tone"})
        assert workspace.audio() == []

    def test_evidence_describes_the_run(self, workspace):
        written(workspace)
        assert workspace.evidence() == []
        RunPatchTool(workspace, lambda *a: result()).run({"name": "tone"})
        assert "440" in workspace.evidence()[0]["text"]

    def test_endpoint(self, monkeypatch):
        monkeypatch.setenv("CHAT_API_KEYS", "k")
        monkeypatch.setenv("ALLOWED_ORIGIN", "http://localhost")
        from fastapi.testclient import TestClient

        import main
        monkeypatch.setattr(main, "CHAT_API_KEYS", {"k"})
        main.limiter.reset()
        client = TestClient(main.app)
        artifact_id = artifacts.put(b"RIFFdata", "audio/wav")
        assert client.get(f"/artifacts/{artifact_id}").status_code in (401, 403)
        r = client.get(f"/artifacts/{artifact_id}", headers={"X-API-Key": "k"})
        assert r.status_code == 200 and r.content == b"RIFFdata"
        assert r.headers["content-type"] == "audio/wav"
        assert client.get("/artifacts/nope", headers={"X-API-Key": "k"}).status_code == 404


class TestRegistry:
    def test_execution_follows_configuration(self, monkeypatch):
        monkeypatch.delenv("PD_SANDBOX_BACKEND", raising=False)
        assert chat.default_registry().names() == ["search_docs", "write_patch"]
        assert "run_patch" not in chat.default_config().system_prompt
        monkeypatch.setenv("PD_SANDBOX_BACKEND", "modal")
        assert chat.default_registry().names() == [
            "search_docs", "write_patch", "run_patch", "analyze_audio"]
        assert "run_patch" in chat.default_config().system_prompt


LONG = "The answer. " * 20


class Script:
    def __init__(self, *turns):
        self.turns = list(turns)

    async def stream_turn(self, request):
        text, calls = self.turns.pop(0)
        if text:
            yield ModelDelta(text)
        yield ModelTurn(
            content=[], text=text,
            tool_calls=tuple(ToolCallRequest(f"c{i}", n, a) for i, (n, a) in enumerate(calls)))


class Search:
    spec = ToolSpec(name="search_docs", description="", input_schema={"type": "object"})

    def run(self, arguments):
        return ToolOutcome(content="x", summary="x", chunks=({
            "id": "1", "heading_path": "h", "text": "t", "url": "u", "source": "s",
            "content_type": "conceptual", "object_name": None},))


def stream(*turns, tools=()):
    loop = AgentLoop(Script(*turns), ToolRegistry([Search(), *tools]))

    async def go():
        return [e async for e in loop.stream("q")]

    return asyncio.run(go()), loop.state


class TestLoop:
    def test_text_before_a_later_tool_call_is_retracted(self):
        events, state = stream(
            ("", [("search_docs", {})]), ("Let me check.", [("search_docs", {})]), ("Answer.", []))
        kinds = [type(e) for e in events]
        assert TextReset in kinds and kinds.index(Sources) < kinds.index(TextDelta)
        assert state.answer == "Answer."

    def test_no_reset_without_text(self):
        events, _ = stream(
            ("", [("search_docs", {})]), ("", [("search_docs", {})]), ("Answer.", []))
        assert not any(isinstance(e, TextReset) for e in events)

    def test_an_answer_written_before_the_last_tool_call_is_not_lost(self):
        events, state = stream(
            ("", [("search_docs", {})]), (LONG, [("search_docs", {})]), ("", []))
        assert state.answer == LONG.strip()
        assert isinstance(events[-1], TextDelta) and events[-1].text == LONG.strip()

    def test_the_model_is_told_when_an_answer_was_discarded(self):
        _, state = stream(
            ("", [("search_docs", {})]), (LONG, [("search_docs", {})]), ("Full answer.", []))
        assert "not shown to the user" in state.messages[4]["content"][-1]["content"]
        _, state = stream(("Let me look.", [("search_docs", {})]), ("Answer.", []))
        assert "not shown" not in state.messages[2]["content"][-1]["content"]

    @pytest.mark.parametrize("search_first", [True, False])
    def test_text_beside_a_write_is_kept(self, search_first):
        workspace = PatchWorkspace()
        first = [("", [("search_docs", {})])] if search_first else []
        events, state = stream(
            *first, (LONG, [("write_patch", ARGS)]), ("A coda.", []),
            tools=[WritePatchTool(workspace)])
        assert state.answer == LONG + "\n\nA coda."
        assert "".join(e.text for e in events if isinstance(e, TextDelta)) == state.answer
        assert not any(isinstance(e, TextReset) for e in events)

    def test_text_beside_a_write_is_dropped_if_the_patch_is_then_run(self):
        workspace = PatchWorkspace()
        events, state = stream(
            ("", [("search_docs", {})]), (LONG, [("write_patch", ARGS)]),
            ("", [("run_patch", {"name": "tone"})]), ("Checked answer.", []),
            tools=[WritePatchTool(workspace), RunPatchTool(workspace, lambda *a: result())])
        assert state.answer == "Checked answer."
        assert any(isinstance(e, TextReset) for e in events)

    def test_write_run_and_revise_in_one_request(self):
        """The model sees the error, fixes the patch, and sees it run."""
        workspace = PatchWorkspace()
        consoles = ["error: ... couldn't create\n", ""]
        tools = [WritePatchTool(workspace),
                 RunPatchTool(workspace, lambda *a: result(consoles.pop(0))),
                 AnalyzeAudioTool(workspace)]
        bad = {**ARGS, "objects": [{**OBJECTS[1], "text": "fm~"}], "connections": []}
        events, state = stream(
            ("", [("write_patch", bad), ("run_patch", {"name": "tone"})]),
            ("", [("write_patch", ARGS), ("run_patch", {"name": "tone"}),
                  ("analyze_audio", {"name": "tone"})]),
            ("Fixed.", []), tools=tools)
        assert state.answer == "Fixed." and state.tool_calls == 5
        entry = workspace.entries["tone"]
        assert entry.revisions == 2 and entry.run.clean
        results = [b["content"] for m in state.messages if m["role"] == "user"
                   and isinstance(m["content"], list) for b in m["content"]]
        assert "couldn't create" in results[1] and "dominant_hz" in results[4]


def _docker_image():
    return shutil.which("docker") and subprocess.run(
        ["docker", "image", "inspect", os.environ.get("PD_SANDBOX_IMAGE", "pd-sandbox:latest")],
        capture_output=True).returncode == 0


@pytest.mark.skipif(
    not os.environ.get("PD_SANDBOX_REQUIRED") and not _docker_image(),
    reason="pd-sandbox image not built")
class TestInTheSandbox:
    def test_simulated_input_sets_the_pitch(self, workspace):
        written(workspace)
        tool = RunPatchTool(workspace, run_patch)
        silent = tool.run({"name": "tone"})
        # osc~ with no input sits at 0 Hz: a constant, not a tone.
        assert json.loads(silent.content)["ran_cleanly"]
        assert workspace.entries["tone"].run.analyzed()["dominant_hz"] is None
        out = tool.run({"name": "tone", "inputs": [{"id": "n", "value": "330", "at_ms": 10}]})
        assert json.loads(out.content)["ran_cleanly"], out.content
        a = workspace.entries["tone"].run.analyzed()
        assert a["segments"][3]["dominant_hz"] == pytest.approx(330, abs=3)

    def test_unknown_object_and_bad_wire(self, workspace):
        written(workspace, objects=[{**OBJECTS[1], "text": "fm~ 3"}, OBJECTS[2]],
                connections=[WIRES[1]])
        out = RunPatchTool(workspace, run_patch).run({"name": "tone"})
        body = json.loads(out.content)
        assert not body["ran_cleanly"] and "fm~ 3" in body["console"]
