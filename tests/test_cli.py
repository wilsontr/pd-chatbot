"""Tests for the pdbot CLI (TASK-5.17)."""

from __future__ import annotations

import asyncio
import io
import json
import subprocess
import sys
import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from agent import artifacts
from agent.cli import main, run_ask, run_list_runs, run_pd_file, run_replay
from agent.events import (
    Artifact,
    Done,
    ModelTurnEvent,
    Sources,
    TextDelta,
    TextReset,
    ToolCall,
    ToolResult,
)
from agent.store import RunStore, event_to_record
from sandbox import SandboxError, SandboxResult

PATCH_DICT = {
    "objects": [
        {"id": "o", "type": "obj", "text": "osc~ 440", "inlets": 2, "outlets": 1},
        {"id": "d", "type": "obj", "text": "dac~", "inlets": 2, "outlets": 0},
    ],
    "connections": [
        {"srcId": "o", "srcOutlet": 0, "dstId": "d", "dstInlet": 0},
    ],
}

SAMPLE_PD = (
    "#N canvas 0 50 600 400 12;\n"
    "#X obj 30 30 osc~ 440;\n"
    "#X obj 30 80 dac~;\n"
    "#X connect 0 0 1 0;\n"
)


def _wav_440(seconds: float = 0.5, rate: int = 48000) -> bytes:
    samples = 0.5 * np.sin(2 * np.pi * 440 * np.arange(int(rate * seconds)) / rate)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((samples * 32767).round().astype("<i2").tobytes())
    return buf.getvalue()


def _sandbox_result(console: str = "", status: str = "ok", audio: bytes | None = None) -> SandboxResult:
    return SandboxResult(
        status=status,
        exit_code=0,
        signal=0,
        console=console,
        console_truncated=False,
        wav=audio if audio is not None else _wav_440(),
        seconds_rendered=0.5,
        peak=0.5,
        nonfinite_samples=0,
    )


class _TTYStringIO(io.StringIO):
    def __init__(self, initial: str = "", *, tty: bool = False):
        super().__init__(initial)
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


class TestAsk:
    def test_streams_answer_and_tool_steps(self):
        seen: list[dict] = []

        async def fake_stream(question: str, **kwargs):
            seen.append({"question": question, **kwargs})
            yield ToolCall(id="c1", name="search_docs", arguments={"query": "osc~"})
            yield ToolResult(
                id="c1",
                name="search_docs",
                ok=True,
                summary="1 section for 'osc~'",
                latency_ms=12.4,
            )
            yield Sources(
                items=({"heading_path": "osc~", "url": "https://example.test/osc~"},),
                query_type="object_reference",
            )
            yield TextDelta("`osc~` is a ")
            yield TextDelta("cosine oscillator.")
            yield Done(
                answer="`osc~` is a cosine oscillator.",
                new_history=[],
                turns=2,
                tool_calls=1,
                run_id="run-123",
                session_id="sess-456",
            )

        out = io.StringIO()
        err = io.StringIO()
        code = asyncio.run(
            run_ask("what is osc~?", stream_fn=fake_stream, stdout=out, stderr=err)
        )

        assert code == 0
        assert seen[0]["interface"] == "cli"
        assert out.getvalue() == "`osc~` is a cosine oscillator.\n"
        err_text = err.getvalue()
        assert "-> search_docs(query='osc~')" in err_text
        assert "<- search_docs [ok (12 ms)]: 1 section for 'osc~'" in err_text
        assert "https://example.test/osc~" in err_text
        assert "run_id=run-123" in err_text

    def test_handles_text_reset(self):
        async def fake_stream(question: str, **kwargs):
            yield TextDelta("Let me check...")
            yield TextReset()
            yield TextDelta("Final answer.")
            yield Done(answer="Final answer.", new_history=[], turns=2, tool_calls=1)

        out = io.StringIO()
        err = io.StringIO()
        code = asyncio.run(run_ask("q", stream_fn=fake_stream, stdout=out, stderr=err))
        assert code == 0
        assert out.getvalue() == "Let me check...\nFinal answer.\n"
        assert "discarded draft text" in err.getvalue()

    def test_writes_pd_and_audio_files_to_out_dir(self, tmp_path):
        audio_bytes = _wav_440(0.25)
        art_id = artifacts.put(audio_bytes, "audio/wav")

        async def fake_stream(question: str, **kwargs):
            yield Artifact(kind="pd_patch", data={"name": "tone", "patch": PATCH_DICT})
            yield Artifact(
                kind="audio",
                data={"id": art_id, "name": "tone", "mime": "audio/wav", "seconds": 0.25},
            )
            yield TextDelta("Built the patch.")
            yield Done(answer="Built the patch.", new_history=[], turns=1, tool_calls=1)

        out = io.StringIO()
        err = io.StringIO()
        code = asyncio.run(
            run_ask("build a tone", stream_fn=fake_stream, out_dir=tmp_path, stdout=out, stderr=err)
        )

        assert code == 0
        pd_path = tmp_path / "tone.pd"
        wav_path = tmp_path / "tone.wav"
        assert pd_path.is_file()
        pd_text = pd_path.read_text(encoding="utf-8")
        assert pd_text.startswith("#N canvas")
        assert "osc~ 440" in pd_text
        assert "dac~" in pd_text
        assert wav_path.is_file()
        assert wav_path.read_bytes() == audio_bytes

    def test_quiet_suppresses_stderr(self):
        async def fake_stream(question: str, **kwargs):
            yield ToolCall(id="c1", name="search_docs", arguments={"query": "osc~"})
            yield ToolResult(id="c1", name="search_docs", ok=True, summary="1 section")
            yield TextDelta("Quiet answer")
            yield Done(answer="Quiet answer", new_history=[], turns=1, tool_calls=1)

        out = io.StringIO()
        err = io.StringIO()
        code = asyncio.run(
            run_ask("q", stream_fn=fake_stream, quiet=True, stdout=out, stderr=err)
        )
        assert code == 0
        assert out.getvalue() == "Quiet answer\n"
        assert err.getvalue() == ""

    def test_main_reads_stdin_and_rejects_empty_tty(self):
        err = io.StringIO()
        code = main(["ask"], stdin=_TTYStringIO("", tty=True), stdout=io.StringIO(), stderr=err)
        assert code == 2
        assert "Provide a question" in err.getvalue()

        seen: list[str] = []

        async def fake_stream(question: str, **kwargs):
            seen.append(question)
            yield TextDelta("ok")
            yield Done(answer="ok", new_history=[], turns=1, tool_calls=0)

        with patch("agent.chat.stream_chat", fake_stream):
            out = io.StringIO()
            code = main(
                ["ask", "--no-log", "-"],
                stdin=_TTYStringIO("how does metro work?\n", tty=False),
                stdout=out,
                stderr=io.StringIO(),
            )
        assert code == 0
        assert seen == ["how does metro work?"]
        assert out.getvalue() == "ok\n"


class TestRun:
    def test_runs_pd_file_and_writes_wav(self, tmp_path):
        pd_file = tmp_path / "tone.pd"
        pd_file.write_text(SAMPLE_PD, encoding="utf-8")
        wav_out = tmp_path / "out" / "tone.wav"
        seen: list[tuple[bytes, float]] = []

        def fake_runner(data: bytes, seconds: float) -> SandboxResult:
            seen.append((data, seconds))
            return _sandbox_result(console="verbose(1): DSP on\n")

        out = io.StringIO()
        err = io.StringIO()
        code = run_pd_file(
            pd_file,
            seconds=1.5,
            wav_out=wav_out,
            runner=fake_runner,
            stdout=out,
            stderr=err,
        )

        assert code == 0
        assert seen[0][1] == 1.5
        assert b"osc~ 440" in seen[0][0]
        assert wav_out.read_bytes()[:4] == b"RIFF"
        text = out.getvalue()
        assert "ran_cleanly=True" in text
        assert "dominant_hz=" in text

    def test_json_output(self, tmp_path):
        pd_file = tmp_path / "tone.pd"
        pd_file.write_text(SAMPLE_PD, encoding="utf-8")
        out = io.StringIO()
        code = run_pd_file(
            pd_file,
            as_json=True,
            runner=lambda *_: _sandbox_result(),
            stdout=out,
            stderr=io.StringIO(),
        )
        assert code == 0
        payload = json.loads(out.getvalue())
        assert payload["ran_cleanly"] is True
        assert payload["errors"] == []
        assert payload["audio"]["silent"] is False

    def test_console_errors_return_nonzero(self, tmp_path):
        pd_file = tmp_path / "bad.pd"
        pd_file.write_text(SAMPLE_PD, encoding="utf-8")
        out = io.StringIO()
        code = run_pd_file(
            pd_file,
            runner=lambda *_: _sandbox_result(console="nope~: ... couldn't create\n"),
            stdout=out,
            stderr=io.StringIO(),
        )
        assert code == 1
        assert "couldn't create" in out.getvalue()

    def test_sandbox_error_missing_file_and_bad_seconds(self, tmp_path):
        err = io.StringIO()
        assert run_pd_file(tmp_path / "missing.pd", stderr=err) == 1
        assert "not found" in err.getvalue()

        pd_file = tmp_path / "tone.pd"
        pd_file.write_text(SAMPLE_PD, encoding="utf-8")
        err2 = io.StringIO()
        assert run_pd_file(pd_file, seconds=25.0, stderr=err2) == 1
        assert "between 0.1 and 10.0" in err2.getvalue()

        err3 = io.StringIO()

        def failing_runner(*_):
            raise SandboxError("docker not found")

        assert run_pd_file(pd_file, runner=failing_runner, stderr=err3) == 1
        assert "Sandbox error: docker not found" in err3.getvalue()


class TestReplayAndRuns:
    def _seed_store(self, tmp_path: Path) -> tuple[RunStore, str]:
        db_url = f"sqlite:///{tmp_path / 'runs.db'}"
        store = RunStore(url=db_url)
        run_id = store.start_run(
            run_id="run-abc",
            session_id="sess-1",
            question="make a 440 Hz tone",
            interface="cli",
            model="claude-test",
        )
        events = [
            ModelTurnEvent(
                turn=1,
                model="claude-test",
                content=({"type": "text", "text": "Building patch"},),
                usage={"input_tokens": 100, "output_tokens": 20},
                cost_usd=0.001,
            ),
            ToolCall(id="t1", name="write_patch", arguments={"name": "tone"}),
            ToolResult(id="t1", name="write_patch", ok=True, summary="wrote tone (2 boxes)", latency_ms=5.0),
            Sources(items=({"heading_path": "osc~", "url": "https://example.test/osc~"},), query_type="patch"),
            TextDelta("Here is a 440 Hz sine patch."),
            Artifact(kind="pd_patch", data={"name": "tone", "patch": PATCH_DICT}),
            Done(
                answer="Here is a 440 Hz sine patch.",
                new_history=[],
                query_type="patch",
                turns=1,
                tool_calls=1,
                run_id=run_id,
                session_id="sess-1",
            ),
        ]
        for seq, ev in enumerate(events):
            ev_type, payload = event_to_record(ev)
            store.append_event(run_id, "sess-1", seq, ev_type, payload)
        store.finish_run(
            run_id,
            status="completed",
            query_type="patch",
            answer="Here is a 440 Hz sine patch.",
            turns=1,
            tool_calls=1,
            cost_usd=0.001,
        )
        return store, db_url

    def test_replay_prints_steps_answer_and_exports_patches(self, tmp_path):
        store, db_url = self._seed_store(tmp_path)
        out_dir = tmp_path / "exported"
        out = io.StringIO()
        err = io.StringIO()

        code = run_replay("run-abc", db_url=db_url, out_dir=out_dir, stdout=out, stderr=err)
        assert code == 0
        text = out.getvalue()
        assert "Run: run-abc" in text
        assert "Question: make a 440 Hz tone" in text
        assert "-> write_patch(name='tone')" in text
        assert "<- write_patch [ok (5 ms)]: wrote tone (2 boxes)" in text
        assert "Here is a 440 Hz sine patch." in text
        assert "https://example.test/osc~" in text
        assert (out_dir / "tone.pd").is_file()
        assert "osc~ 440" in (out_dir / "tone.pd").read_text(encoding="utf-8")

        out_json = io.StringIO()
        assert run_replay("run-abc", store=store, as_json=True, stdout=out_json) == 0
        state = json.loads(out_json.getvalue())
        assert state["run_id"] == "run-abc"
        assert state["answer"] == "Here is a 440 Hz sine patch."

    def test_replay_unknown_run_fails(self, tmp_path):
        db_url = f"sqlite:///{tmp_path / 'empty.db'}"
        RunStore(url=db_url)
        err = io.StringIO()
        code = run_replay("missing-id", db_url=db_url, stdout=io.StringIO(), stderr=err)
        assert code == 1
        assert "not found" in err.getvalue()

    def test_runs_lists_stored_runs(self, tmp_path):
        _, db_url = self._seed_store(tmp_path)
        out = io.StringIO()
        code = main(["runs", "--db", db_url], stdout=out, stderr=io.StringIO())
        assert code == 0
        assert "run-abc" in out.getvalue()
        assert "make a 440 Hz tone" in out.getvalue()

        out_json = io.StringIO()
        assert run_list_runs(db_url=db_url, as_json=True, stdout=out_json) == 0
        rows = json.loads(out_json.getvalue())
        assert len(rows) == 1
        assert rows[0]["run_id"] == "run-abc"


def test_installed_pdbot_console_script_help(tmp_path):
    pdbot_bin = Path(sys.executable).parent / "pdbot"
    if not pdbot_bin.exists():
        pytest.skip("pdbot console script not installed in current python bin")
    proc = subprocess.run(
        [str(pdbot_bin), "--help"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 0
    for cmd in ("ask", "run", "replay", "runs"):
        assert cmd in proc.stdout
