"""Patch evaluation: do the agent's patches run, and do what was asked?

Each task in ``patch_tasks.json`` asks for a patch and has a machine-checked spec
(pitch, level, spectrum, envelope, printed output). The final patches of every
answer are run in the sandbox here, for both arms, so the arms differ only in
whether the agent could run its patches while answering::

    python -m evaluation.patch_eval --trials 3 --report report.md --output results.json

No LLM judges the result. A task passes when any patch in the answer runs without
a Pd error and meets every check in the spec.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import random
import statistics
import time
import wave
from pathlib import Path
from typing import Any, Mapping

import numpy as np

import pricing
from agent.audio import analyze, read_wav
from agent.tools.patch import clean_console, console_errors, parse_inputs, runnable_file
from evaluation.retry import _is_retryable
from model_config import ANSWER_MODEL
from pd_schema import PdPatch
from sandbox import SandboxResult, run_patch

TASKS_PATH = Path(__file__).parent / "patch_tasks.json"
ARMS = ("without", "with")
SECONDS = 2.0
_SPEC_KEYS = {
    "hz", "hz_tol", "peak", "stereo", "peaks_hz", "absent_hz", "max_clipped", "max_dc",
    "centroid", "segment_hz", "segment_tol", "decays", "inputs", "prints", "loud_segments",
    "quiet_segments", "channel_hz", "tremolo_hz",
}


def load_tasks(path: Path = TASKS_PATH) -> list[dict[str, Any]]:
    tasks = json.loads(path.read_text())
    for task in tasks:
        unknown = set(task["spec"]) - _SPEC_KEYS
        if unknown or not task["spec"]:
            raise ValueError(f"{task['id']}: bad spec {sorted(unknown)}")
    if len({t["id"] for t in tasks}) != len(tasks):
        raise ValueError("duplicate task id")
    return tasks


def _magnitude_db(mono: np.ndarray, rate: int, hz: float) -> float:
    """Level near ``hz`` relative to the strongest component."""
    mag = np.abs(np.fft.rfft((mono - mono.mean()) * np.hanning(len(mono))))
    freqs = np.fft.rfftfreq(len(mono), 1 / rate)
    near = mag[np.abs(freqs - hz) <= max(3.0, hz * 0.01)]
    return float(20 * np.log10((near.max() + 1e-12) / (mag.max() + 1e-12)))


def _mono_wav(samples: np.ndarray, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((samples * 32767).round().astype("<i2").tobytes())
    return buf.getvalue()


def _modulation_hz(mono: np.ndarray, rate: int) -> float | None:
    """Rate of the strongest level change between 1 and 20 Hz."""
    hop = rate // 200
    frames = mono[: len(mono) // hop * hop].reshape(-1, hop)
    level = np.sqrt((frames**2).mean(axis=1))
    mag = np.abs(np.fft.rfft((level - level.mean()) * np.hanning(len(level)), 4096))
    freqs = np.fft.rfftfreq(4096, hop / rate)
    band = (freqs >= 1) & (freqs <= 20)
    # A steady tone leaves only measurement ripple here.
    if level.mean() == 0 or level.std() / level.mean() < 0.05:
        return None
    return float(freqs[band][mag[band].argmax()])


def check_spec(spec: Mapping[str, Any], result: SandboxResult) -> list[str]:
    """Every way the run misses the spec; empty means it meets it."""
    problems: list[str] = []
    if "prints" in spec:
        printed = [
            line.split(":", 1)[1].strip()
            for line in clean_console(result.console).splitlines() if ":" in line
        ]
        wanted = list(spec["prints"])
        if not any(printed[i : i + len(wanted)] == wanted for i in range(len(printed) + 1)):
            problems.append(f"printed {printed[:8]}, wanted {wanted}")
        return problems

    a = analyze(result.wav)
    if a["silent"]:
        return ["silent"]
    samples, rate = read_wav(result.wav)
    mono = samples[:, int(np.abs(samples).mean(axis=0).argmax())]

    def near(label: str, got: float | None, want: float, tol: float) -> None:
        if got is None or abs(got - want) > tol:
            problems.append(f"{label} is {got}, wanted {want} ± {tol:g}")

    if "hz" in spec:
        near("dominant_hz", a["dominant_hz"], spec["hz"], spec.get("hz_tol", max(3, spec["hz"] * 0.01)))
    if "peak" in spec and not spec["peak"][0] <= a["peak"] <= spec["peak"][1]:
        problems.append(f"peak is {a['peak']}, wanted {spec['peak']}")
    if spec.get("stereo") and min(a["channel_rms"]) < 0.5 * max(a["channel_rms"]):
        problems.append(f"channels are uneven: {a['channel_rms']}")
    for hz in spec.get("peaks_hz", []):
        level = _magnitude_db(mono, rate, hz)
        if level < -30:
            problems.append(f"{hz} Hz is missing ({level:.0f} dB)")
    for hz in spec.get("absent_hz", []):
        level = _magnitude_db(mono, rate, hz)
        if level > -30:
            problems.append(f"{hz} Hz should be absent ({level:.0f} dB)")
    if "max_clipped" in spec and (
        a["clipped_fraction"] > spec["max_clipped"] + 0.0005 or result.peak > 1.001
    ):
        problems.append(f"clips (peak {result.peak:.2f})")
    if "max_dc" in spec and abs(a["dc_offset"]) > spec["max_dc"]:
        problems.append(f"dc offset {a['dc_offset']}")
    if "centroid" in spec:
        low, high = spec["centroid"]
        if not low <= (a["spectral_centroid_hz"] or 0) <= high:
            problems.append(f"centroid is {a['spectral_centroid_hz']}, wanted {low}-{high}")
    segments = a["segments"]
    for index, hz in spec.get("segment_hz", {}).items():
        near(f"segment {index} hz", segments[int(index)]["dominant_hz"], hz,
             spec.get("segment_tol", max(6, hz * 0.02)))
    loudest = max(s["rms"] for s in segments)
    for index in spec.get("loud_segments", []):
        if segments[index]["rms"] < 0.05 * loudest:
            problems.append(f"segment {index} should sound")
    for index in spec.get("quiet_segments", []):
        if segments[index]["rms"] > 0.02 * loudest:
            problems.append(f"segment {index} should be quiet")
    if spec.get("decays"):
        # Sounds at the start, never gets louder, and is gone by the end. The
        # attack may spill into the second segment.
        levels = [s["rms"] for s in segments]
        if levels[-1] > 0.02 * loudest or max(levels[:2]) < loudest or any(
            b > a_ * 1.1 + 0.001 for a_, b in zip(levels[1:], levels[2:])
        ):
            problems.append(f"does not fade: {levels}")
        elif levels[2] < 0.02 * loudest:
            problems.append("stops too soon to be a one-second fade")
    if "channel_hz" in spec:
        for channel, hz in enumerate(spec["channel_hz"]):
            if channel >= samples.shape[1]:
                problems.append("mono")
                continue
            one = samples[:, channel]
            near(f"channel {channel} hz", analyze(_mono_wav(one, rate)).get("dominant_hz"), hz, 5)
            for other in spec["channel_hz"]:
                if other != hz and _magnitude_db(one, rate, other) > -30:
                    problems.append(f"channel {channel} also has {other} Hz")
    if "tremolo_hz" in spec:
        near("tremolo", _modulation_hz(mono, rate), spec["tremolo_hz"], 0.5)
    return problems


def spec_inputs(spec: Mapping[str, Any], patch: PdPatch) -> list[dict[str, Any]]:
    """Resolve the spec's inputs to this patch's boxes, by box type."""
    out = []
    for item in spec.get("inputs", []):
        box = next(
            (o for t in item["types"] for o in patch.objects
             if o.type == t or (o.type == "obj" and o.text.split()[:1] == [t])),
            None,
        )
        if box is not None:
            out.append({"id": box.id, "value": item["value"], "at_ms": item["at_ms"]})
    return parse_inputs(out, patch, SECONDS)


def score_patch(spec: Mapping[str, Any], patch: PdPatch, runner=run_patch) -> dict[str, Any]:
    result = runner(runnable_file(patch, spec_inputs(spec, patch)), SECONDS)
    errors = console_errors(result.console)
    clean = result.ok and not errors
    problems = check_spec(spec, result)
    return {
        "clean": clean, "status": result.status, "errors": errors[:5],
        "meets_spec": not problems, "problems": problems, "passed": clean and not problems,
    }


async def run_task(task: dict[str, Any], arm: str, model: str) -> dict[str, Any]:
    from agent.chat import answer_question, default_config

    execution = arm == "with"
    for attempt in range(4):
        started = time.perf_counter()
        try:
            answer = await answer_question(
                task["question"], execution=execution,
                config=default_config(execution=execution, model=model),
            )
            break
        except Exception as exc:
            if attempt == 3 or not _is_retryable(exc):
                raise
            await asyncio.sleep(2 ** attempt * 3 + random.random())
    latency = time.perf_counter() - started
    scores = [
        await asyncio.to_thread(score_patch, task["spec"], PdPatch.model_validate(p["patch"]))
        for p in answer.patches
    ]
    entries = answer.workspace.entries.values()
    return {
        "id": task["id"],
        "arm": arm,
        "wrote_patch": bool(scores),
        "clean": any(s["clean"] for s in scores),
        "passed": any(s["passed"] for s in scores),
        "scores": scores,
        "revisions": sum(e.revisions for e in entries),
        "runs": answer.workspace.runs,
        "turns": answer.turns,
        "tool_calls": answer.tool_calls,
        "stop_reason": answer.stop_reason,
        "latency_s": round(latency, 2),
        "cost_usd": pricing.cost_usd(model, answer.usage),
        "patches": answer.patches,
        "answer": answer.prose,
    }


async def run_eval(
    tasks: list[dict[str, Any]], arms: tuple[str, ...], trials: int, model: str, workers: int
) -> list[dict[str, Any]]:
    gate = asyncio.Semaphore(workers)

    async def one(task, arm, trial):
        async with gate:
            try:
                row = await run_task(task, arm, model)
            except Exception as exc:
                # Reported apart from failures: an outage is not a bad patch.
                row = {"id": task["id"], "arm": arm, "error": repr(exc)[:300]}
            row["trial"] = trial
            mark = "ERROR" if "error" in row else "pass" if row["passed"] else "fail"
            print(f"{task['id']} {arm:8} trial {trial}: {mark}", flush=True)
            return row

    return list(await asyncio.gather(*(
        one(task, arm, trial)
        for trial in range(1, trials + 1) for task in tasks for arm in arms
    )))


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for arm in ARMS:
        mine = [r for r in rows if r["arm"] == arm]
        scored = [r for r in mine if "error" not in r]
        if not scored:
            continue
        trials = sorted({r["trial"] for r in scored})
        per_trial = [
            statistics.mean(r["passed"] for r in scored if r["trial"] == t) for t in trials
        ]
        costs = [r["cost_usd"] for r in scored if r["cost_usd"] is not None]
        latencies = sorted(r["latency_s"] for r in scored)
        out[arm] = {
            "n": len(scored),
            "infrastructure_errors": len(mine) - len(scored),
            "wrote_patch": statistics.mean(r["wrote_patch"] for r in scored),
            "clean": statistics.mean(r["clean"] for r in scored),
            "passed": statistics.mean(r["passed"] for r in scored),
            "passed_by_trial": [round(v, 3) for v in per_trial],
            "cost_usd": statistics.mean(costs) if costs else None,
            "latency_p50_s": statistics.median(latencies),
            "latency_p95_s": latencies[max(0, round(0.95 * len(latencies)) - 1)],
            "tool_calls": statistics.mean(r["tool_calls"] for r in scored),
            "runs": statistics.mean(r["runs"] for r in scored),
        }
    return out


def to_markdown(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    names = {"without": "Without execution", "with": "With execution"}
    arms = [a for a in ARMS if a in summary]
    lines = [
        "| | " + " | ".join(names[a] for a in arms) + " |",
        "|---|" + "---|" * len(arms),
    ]
    for label, key, fmt in (
        ("Answers", "n", "{}"),
        ("Wrote a patch", "wrote_patch", "{:.0%}"),
        ("Runs cleanly", "clean", "{:.0%}"),
        ("**Runs cleanly and meets the spec**", "passed", "**{:.0%}**"),
        ("By trial", "passed_by_trial", "{}"),
        ("Cost per answer", "cost_usd", "${:.4f}"),
        ("Latency p50 / s", "latency_p50_s", "{:.1f}"),
        ("Latency p95 / s", "latency_p95_s", "{:.1f}"),
        ("Tool calls", "tool_calls", "{:.1f}"),
        ("Patch runs", "runs", "{:.1f}"),
        ("Infrastructure errors (excluded)", "infrastructure_errors", "{}"),
    ):
        cells = [
            "n/a" if summary[a][key] is None else fmt.format(summary[a][key]) for a in arms
        ]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines += ["", "| Task | " + " | ".join(names[a] for a in arms) + " |", "|---|" + "---|" * len(arms)]
    for task in sorted({r["id"] for r in rows}):
        cells = []
        for arm in arms:
            mine = [r for r in rows if r["id"] == task and r["arm"] == arm and "error" not in r]
            cells.append(f"{sum(r['passed'] for r in mine)}/{len(mine)}")
        lines.append(f"| {task} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--arm", choices=(*ARMS, "both"), default="both")
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--task", action="append", help="Only this task id (repeatable)")
    parser.add_argument("--model", default=ANSWER_MODEL)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    # Scoring needs a sandbox in both arms.
    os.environ.setdefault("PD_SANDBOX_BACKEND", "docker")

    tasks = [t for t in load_tasks() if not args.task or t["id"] in args.task]
    arms = ARMS if args.arm == "both" else (args.arm,)
    rows = asyncio.run(run_eval(tasks, arms, args.trials, args.model, args.workers))
    summary = summarize(rows)
    report = to_markdown(summary, rows)
    print("\n" + report)
    if args.report:
        args.report.write_text(report)
    if args.output:
        args.output.write_text(json.dumps({"summary": summary, "rows": rows}, indent=1))


if __name__ == "__main__":
    main()
