"""The ``pdbot`` CLI: terminal interface over the agent core (TASK-5.17).

Subcommands:
- ``pdbot ask``    Stream an answer with tool steps, optionally saving ``.pd`` files.
- ``pdbot run``    Run a local ``.pd`` file in the Pd sandbox and report console + audio.
- ``pdbot replay`` Replay a stored run from ``RunStore``, optionally exporting ``.pd`` files.
- ``pdbot runs``   List recent stored runs so their ``run_id`` can be passed to ``replay``.

Imports of heavy modules (``agent.chat``, ``agent.store``, ``sandbox``) stay
inside each subcommand so ``pdbot --help``, ``run``, and ``replay`` do not touch
ChromaDB or require API keys.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, TextIO

from .events import (
    AgentEvent,
    ApprovalRequested,
    ApprovalResolved,
    Artifact,
    Done,
    Sources,
    TextDelta,
    TextReset,
    ToolCall,
    ToolResult,
)


def _format_args(arguments: Mapping[str, Any]) -> str:
    if not arguments:
        return ""
    parts: list[str] = []
    for key, val in arguments.items():
        if isinstance(val, list) and len(val) > 3:
            rendered = f"[{len(val)} items]"
        else:
            rendered = repr(val)
            if len(rendered) > 60:
                rendered = rendered[:57] + "..."
        parts.append(f"{key}={rendered}")
    return ", ".join(parts)


def _safe_filename(name: str, ext: str) -> str:
    base = Path(name.strip() or "patch").name
    if base in ("", ".", ".."):
        base = "patch"
    return base if base.endswith(ext) else f"{base}{ext}"


def _write_patch_file(out_dir: Path, name: str, patch_data: Mapping[str, Any]) -> Path:
    from pd_file import to_pd
    from pd_schema import PdPatch

    patch = PdPatch.model_validate(patch_data)
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / _safe_filename(name, ".pd")
    dest.write_text(to_pd(patch), encoding="utf-8")
    return dest


def _write_audio_file(out_dir: Path, name: str, wav_bytes: bytes) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / _safe_filename(name, ".wav")
    dest.write_bytes(wav_bytes)
    return dest


async def run_ask(
    question: str,
    *,
    stream_fn: Callable[..., Any] | None = None,
    out_dir: Path | None = None,
    session_id: str | None = None,
    run_store: Any = None,
    patch_store: Any = None,
    quiet: bool = False,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Stream one question through the agent core to ``stdout`` and steps to ``stderr``."""
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr

    if stream_fn is None:
        from .chat import stream_chat

        stream_fn = stream_chat

    sources: list[Mapping[str, Any]] = []
    wrote_text = False
    last_char = ""
    done_event: Done | None = None

    async for event in stream_fn(
        question,
        interface="cli",
        session_id=session_id,
        run_store=run_store,
        patch_store=patch_store,
    ):
        if isinstance(event, ToolCall):
            if not quiet:
                args_str = _format_args(event.arguments)
                err.write(f"-> {event.name}({args_str})\n")
                err.flush()
        elif isinstance(event, ToolResult):
            if not quiet:
                status = "ok" if event.ok else "err"
                timing = f" ({event.latency_ms:.0f} ms)" if event.latency_ms is not None else ""
                err.write(f"<- {event.name} [{status}{timing}]: {event.summary}\n")
                err.flush()
        elif isinstance(event, TextDelta):
            if event.text:
                out.write(event.text)
                out.flush()
                wrote_text = True
                last_char = event.text[-1]
        elif isinstance(event, TextReset):
            if wrote_text:
                if last_char != "\n":
                    out.write("\n")
                    out.flush()
                wrote_text = False
                last_char = ""
            if not quiet:
                err.write("[discarded draft text before tool call]\n")
                err.flush()
        elif isinstance(event, Sources):
            sources = list(event.items)
        elif isinstance(event, Artifact):
            if event.kind == "pd_patch":
                name = str(event.data.get("name") or "patch")
                patch_data = event.data.get("patch")
                if out_dir is not None and isinstance(patch_data, Mapping):
                    dest = _write_patch_file(out_dir, name, patch_data)
                    if not quiet:
                        err.write(f"Wrote {dest}\n")
                        err.flush()
                elif not quiet:
                    err.write(
                        f"Patch {name!r} produced (pass -o DIR to save .pd files)\n"
                    )
                    err.flush()
            elif event.kind == "audio" and out_dir is not None:
                from . import artifacts

                art = artifacts.get(str(event.data.get("id") or ""))
                if art is not None:
                    name = str(event.data.get("name") or "audio")
                    dest = _write_audio_file(out_dir, name, art.data)
                    if not quiet:
                        err.write(f"Wrote {dest}\n")
                        err.flush()
        elif isinstance(event, Done):
            done_event = event

    if wrote_text and last_char != "\n":
        out.write("\n")
        out.flush()

    if not quiet:
        seen_urls: set[str] = set()
        unique_sources: list[Mapping[str, Any]] = []
        for item in sources:
            url = str(item.get("url") or "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                unique_sources.append(item)
        if unique_sources:
            err.write("\nSources:\n")
            for item in unique_sources:
                heading = item.get("heading_path") or item.get("object_name") or item.get("url")
                err.write(f"  - {heading}: {item.get('url')}\n")
        if done_event is not None:
            parts = [
                f"turns={done_event.turns}",
                f"tool_calls={done_event.tool_calls}",
            ]
            if done_event.run_id:
                parts.insert(0, f"run_id={done_event.run_id}")
            if done_event.session_id:
                parts.append(f"session_id={done_event.session_id}")
            if done_event.cached:
                parts.append("cached=true")
            if done_event.budget_exceeded:
                parts.append(f"budget_exceeded={done_event.budget_exceeded}")
            err.write(f"[{' '.join(parts)}]\n")
        err.flush()

    return 0


def run_pd_file(
    path: Path,
    *,
    seconds: float = 2.0,
    wav_out: Path | None = None,
    as_json: bool = False,
    runner: Callable[..., Any] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run a local ``.pd`` file in the headless Pd sandbox and report results."""
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr

    if not 0.1 <= seconds <= 10.0:
        err.write("seconds must be between 0.1 and 10.0.\n")
        return 1
    if not path.is_file():
        err.write(f"Patch file not found: {path}\n")
        return 1

    pd_bytes = path.read_bytes()

    from agent.audio import analyze
    from agent.tools.patch import clean_console, console_errors
    from sandbox import SandboxError, run_patch as sandbox_run

    run_fn = runner if runner is not None else sandbox_run
    try:
        result = run_fn(pd_bytes, seconds)
    except (SandboxError, ValueError) as exc:
        err.write(f"Sandbox error: {exc}\n")
        return 1

    console = clean_console(result.console)
    errors = console_errors(result.console)
    audio = analyze(result.wav) if result.wav else None
    ran_cleanly = bool(result.ok and not errors)

    if wav_out is not None and result.wav:
        wav_out.parent.mkdir(parents=True, exist_ok=True)
        wav_out.write_bytes(result.wav)
        if not as_json:
            err.write(f"Wrote {wav_out}\n")

    report = {
        "file": str(path),
        "status": result.status,
        "exit_code": result.exit_code,
        "ran_cleanly": ran_cleanly,
        "seconds_rendered": round(result.seconds_rendered, 3),
        "errors": errors,
        "console": console,
        "console_truncated": result.console_truncated,
        "peak_before_clipping": round(result.peak, 4),
        "nonfinite_samples": result.nonfinite_samples,
        "audio": audio,
    }

    if as_json:
        out.write(json.dumps(report, indent=2) + "\n")
    else:
        out.write(
            f"status: {result.status} (ran_cleanly={ran_cleanly}, "
            f"seconds={report['seconds_rendered']})\n"
        )
        if errors:
            out.write("errors:\n")
            for line in errors:
                out.write(f"  {line}\n")
        if console.strip():
            out.write("console:\n")
            for line in console.strip().splitlines():
                out.write(f"  {line}\n")
        if audio is not None:
            if audio.get("silent"):
                out.write("audio: silent\n")
            else:
                out.write(
                    f"audio: peak={audio.get('peak')} rms_db={audio.get('rms_db')} "
                    f"dominant_hz={audio.get('dominant_hz')} "
                    f"clipped_fraction={audio.get('clipped_fraction')}\n"
                )
        if result.nonfinite_samples:
            out.write(f"nonfinite_samples: {result.nonfinite_samples}\n")
    out.flush()
    return 0 if ran_cleanly else 1


def run_replay(
    run_id: str,
    *,
    store: Any = None,
    db_url: str | None = None,
    out_dir: Path | None = None,
    as_json: bool = False,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Replay a stored agent run from ``RunStore``."""
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr

    if store is None:
        from .store import RunStore

        store = RunStore(url=db_url)

    run = store.get_run(run_id)
    if run is None:
        err.write(f"Run {run_id!r} not found.\n")
        return 1

    state = store.reconstruct_state(run_id)
    events: list[AgentEvent] = store.replay_events(run_id)

    written_paths: list[Path] = []
    if out_dir is not None:
        for patch_item in state.get("patches") or []:
            patch_data = patch_item.get("patch")
            if isinstance(patch_data, Mapping):
                name = str(patch_item.get("name") or "patch")
                written_paths.append(_write_patch_file(out_dir, name, patch_data))

    if as_json:
        out.write(json.dumps(state, indent=2, default=str) + "\n")
        out.flush()
        return 0

    out.write(f"Run: {run['run_id']} (session {run['session_id']})\n")
    meta_parts = [
        f"status={run['status']}",
        f"interface={run['interface']}",
        f"turns={state['turns']}",
        f"tool_calls={state['tool_calls']}",
    ]
    if run.get("model"):
        meta_parts.append(f"model={run['model']}")
    if state.get("cost_usd") is not None:
        meta_parts.append(f"cost_usd=${state['cost_usd']:.6f}")
    if run.get("created_at"):
        meta_parts.append(f"created_at={run['created_at']}")
    out.write("Meta: " + " ".join(meta_parts) + "\n")
    out.write(f"Question: {run['question']}\n")

    step_lines: list[str] = []
    for ev in events:
        if isinstance(ev, ToolCall):
            step_lines.append(f"  -> {ev.name}({_format_args(ev.arguments)})")
        elif isinstance(ev, ToolResult):
            status = "ok" if ev.ok else "err"
            timing = f" ({ev.latency_ms:.0f} ms)" if ev.latency_ms is not None else ""
            step_lines.append(f"  <- {ev.name} [{status}{timing}]: {ev.summary}")
        elif isinstance(ev, ApprovalRequested):
            step_lines.append(
                f"  [approval requested] {ev.tool_name} ({ev.interface}): {ev.reason}"
            )
        elif isinstance(ev, ApprovalResolved):
            decision = "approved" if ev.approved else ("timed out" if ev.timed_out else "denied")
            step_lines.append(f"  [approval {decision}] {ev.tool_name}")

    if step_lines:
        out.write("\nSteps:\n")
        for line in step_lines:
            out.write(line + "\n")

    answer = state.get("answer") or ""
    if answer:
        out.write(f"\nAnswer:\n{answer.rstrip()}\n")

    if state.get("sources"):
        out.write("\nSources:\n")
        for item in state["sources"]:
            heading = item.get("heading_path") or item.get("object_name") or item.get("url")
            out.write(f"  - {heading}: {item.get('url')}\n")

    if state.get("patches"):
        names = [str(p.get("name") or "patch") for p in state["patches"]]
        out.write(f"\nPatches: {', '.join(names)}\n")
        for dest in written_paths:
            out.write(f"  wrote {dest}\n")

    out.flush()
    return 0


def run_list_runs(
    *,
    limit: int = 20,
    session_id: str | None = None,
    store: Any = None,
    db_url: str | None = None,
    as_json: bool = False,
    stdout: TextIO | None = None,
) -> int:
    """List recent runs from ``RunStore``."""
    out = stdout if stdout is not None else sys.stdout
    if store is None:
        from .store import RunStore

        store = RunStore(url=db_url)

    rows = store.list_runs(session_id=session_id, limit=limit)
    if as_json:
        out.write(json.dumps(rows, indent=2, default=str) + "\n")
        out.flush()
        return 0

    if not rows:
        out.write("No stored runs.\n")
        out.flush()
        return 0

    for row in rows:
        q = str(row.get("question") or "").replace("\n", " ")
        if len(q) > 60:
            q = q[:57] + "..."
        out.write(
            f"{row['run_id']}  {row['status']:<10}  {row['interface']:<5}  "
            f"turns={row['turns']}  {q}\n"
        )
    out.flush()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdbot",
        description="Terminal interface for the pd-chatbot Pure Data agent.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    ask_p = sub.add_parser(
        "ask",
        help="Ask a Pure Data question with streamed output and tool steps.",
    )
    ask_p.add_argument(
        "question",
        nargs="*",
        help="Question to ask (omit or pass '-' to read from stdin).",
    )
    ask_p.add_argument(
        "-o",
        "--out-dir",
        type=Path,
        default=None,
        help="Directory to write generated .pd (and rendered .wav) files.",
    )
    ask_p.add_argument(
        "--session",
        default=None,
        help="Session id to continue an earlier conversation's patch workspace.",
    )
    ask_p.add_argument(
        "--db",
        default=None,
        help="Database URL for run and session patch storage (defaults to local SQLite).",
    )
    ask_p.add_argument(
        "--no-log",
        action="store_true",
        help="Do not persist the run or session patches to the store.",
    )
    ask_p.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Only print the streamed answer to stdout; suppress step logs on stderr.",
    )

    run_p = sub.add_parser(
        "run",
        help="Run a local .pd file in the headless Pd sandbox.",
    )
    run_p.add_argument("file", type=Path, help="Path to the .pd file to run.")
    run_p.add_argument(
        "-s",
        "--seconds",
        type=float,
        default=2.0,
        help="Audio duration to render in seconds (0.1 to 10.0, default 2.0).",
    )
    run_p.add_argument(
        "-w",
        "--wav",
        type=Path,
        default=None,
        help="Optional path to save the rendered WAV audio.",
    )
    run_p.add_argument(
        "--json",
        action="store_true",
        help="Print the run and audio analysis report as JSON.",
    )

    replay_p = sub.add_parser(
        "replay",
        help="Replay a stored run by run_id.",
    )
    replay_p.add_argument("run_id", help="Stored run id to replay.")
    replay_p.add_argument(
        "--db",
        default=None,
        help="Database URL for the run store (defaults to local SQLite).",
    )
    replay_p.add_argument(
        "-o",
        "--out-dir",
        type=Path,
        default=None,
        help="Optional directory to write .pd files produced during the run.",
    )
    replay_p.add_argument(
        "--json",
        action="store_true",
        help="Print the reconstructed run state as JSON.",
    )

    runs_p = sub.add_parser(
        "runs",
        help="List recent stored runs.",
    )
    runs_p.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of runs to show (default 20).",
    )
    runs_p.add_argument(
        "--session",
        default=None,
        help="Filter runs by session id.",
    )
    runs_p.add_argument(
        "--db",
        default=None,
        help="Database URL for the run store (defaults to local SQLite).",
    )
    runs_p.add_argument(
        "--json",
        action="store_true",
        help="Print the run list as JSON.",
    )

    return parser


def _resolve_question(raw_parts: list[str], stdin: TextIO) -> str | None:
    joined = " ".join(raw_parts).strip()
    if joined and joined != "-":
        return joined
    if joined == "-" or not stdin.isatty():
        text = stdin.read().strip()
        return text or None
    return None


def main(
    argv: list[str] | None = None,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    in_stream = stdin if stdin is not None else sys.stdin
    out_stream = stdout if stdout is not None else sys.stdout
    err_stream = stderr if stderr is not None else sys.stderr

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "ask":
        question = _resolve_question(args.question, in_stream)
        if not question:
            err_stream.write("Provide a question or pipe one on stdin.\n")
            return 2

        run_store = None
        patch_store = None
        if not args.no_log:
            from .sessions import SessionPatchStore
            from .store import RunStore

            run_store = RunStore(url=args.db)
            patch_store = SessionPatchStore(engine=run_store.engine)

        return asyncio.run(
            run_ask(
                question,
                out_dir=args.out_dir,
                session_id=args.session,
                run_store=run_store,
                patch_store=patch_store,
                quiet=args.quiet,
                stdout=out_stream,
                stderr=err_stream,
            )
        )

    if args.command == "run":
        return run_pd_file(
            args.file,
            seconds=args.seconds,
            wav_out=args.wav,
            as_json=args.json,
            stdout=out_stream,
            stderr=err_stream,
        )

    if args.command == "replay":
        return run_replay(
            args.run_id,
            db_url=args.db,
            out_dir=args.out_dir,
            as_json=args.json,
            stdout=out_stream,
            stderr=err_stream,
        )

    if args.command == "runs":
        return run_list_runs(
            limit=args.limit,
            session_id=args.session,
            db_url=args.db,
            as_json=args.json,
            stdout=out_stream,
        )

    parser.print_help(err_stream)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
