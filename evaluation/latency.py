"""Measure answer latency and time-to-first-token (TTFT) per model.

The generation eval scores answers from non-streaming calls, so it can report
wall time but not TTFT — the number that actually matters for the streaming UI,
which starts rendering on the first token. This module streams the same messages
the app sends (same retrieval, same system prompt) and records:

  * ttft_ms  — request sent to first text delta
  * total_ms — request sent to the last delta
  * chars    — size of the streamed answer

Usage:
    python -m evaluation.latency --models claude-sonnet-4-6 claude-haiku-5-5
    python -m evaluation.latency --sample 10 --output docs/latency.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import anthropic

from evaluation.dataset import GoldenDataset
from evaluation.generation import (
    _get_deepseek_client,
    _is_deepseek_model,
    _max_tokens_for,
)
from model_config import thinking_kwargs
from rag import _build_chat_messages, retrieve


def _percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile on an ascending-sorted copy."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round(pct / 100 * len(ordered))) - 1))
    return ordered[index]


def aggregate_latencies(measurements: list[dict[str, Any]]) -> dict[str, Any]:
    """Mean/p50/p95 for ttft and total across measured questions."""
    if not measurements:
        return {"n": 0}
    ttfts = [m["ttft_ms"] for m in measurements if m.get("ttft_ms") is not None]
    totals = [m["total_ms"] for m in measurements if m.get("total_ms") is not None]
    return {
        "n": len(measurements),
        "ttft_mean_ms": round(statistics.mean(ttfts), 1) if ttfts else None,
        "ttft_p50_ms": round(statistics.median(ttfts), 1) if ttfts else None,
        "ttft_p95_ms": round(_percentile(ttfts, 95), 1) if ttfts else None,
        "total_mean_ms": round(statistics.mean(totals), 1) if totals else None,
        "total_p50_ms": round(statistics.median(totals), 1) if totals else None,
        "total_p95_ms": round(_percentile(totals, 95), 1) if totals else None,
        "avg_chars": round(statistics.mean([m["chars"] for m in measurements]), 0),
    }


def measure_stream(
    client: anthropic.Anthropic,
    model: str,
    question: str,
    chunks: list[dict[str, Any]],
    history: list[dict[str, str]] | None = None,
    system_prompt: str | None = None,
) -> dict[str, Any]:
    """Stream one answer and return ttft_ms / total_ms / chars."""
    history = history or []
    system_block, messages = _build_chat_messages(
        question, chunks, history, system_prompt=system_prompt
    )
    started = time.perf_counter()
    ttft_ms: float | None = None
    chars = 0

    if _is_deepseek_model(model):
        system_text = "\n\n".join(block["text"] for block in system_block)
        stream = _get_deepseek_client().chat.completions.create(
            model=model,
            max_tokens=_max_tokens_for(model),
            messages=[{"role": "system", "content": system_text}] + messages,
            stream=True,
        )
        for chunk in stream:
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            text = getattr(delta, "content", None) if delta is not None else None
            if text:
                if ttft_ms is None:
                    ttft_ms = (time.perf_counter() - started) * 1000
                chars += len(text)
    else:
        with client.messages.stream(
            model=model,
            max_tokens=_max_tokens_for(model),
            system=system_block,
            messages=messages,
            **thinking_kwargs(model),
        ) as stream:
            for text in stream.text_stream:
                if text:
                    if ttft_ms is None:
                        ttft_ms = (time.perf_counter() - started) * 1000
                    chars += len(text)

    total_ms = (time.perf_counter() - started) * 1000
    return {
        "question": question,
        "ttft_ms": round(ttft_ms, 1) if ttft_ms is not None else None,
        "total_ms": round(total_ms, 1),
        "chars": chars,
    }


def run_latency_probe(
    dataset: GoldenDataset,
    models: list[str],
    sample: int | None = None,
    system_prompt: str | None = None,
    verbose: bool = False,
) -> dict[str, Any]:
    entries = dataset.entries[:sample] if sample else dataset.entries
    client = anthropic.Anthropic()
    results: dict[str, Any] = {"n": len(entries), "per_model": {}}

    for model in models:
        measurements: list[dict[str, Any]] = []
        for i, entry in enumerate(entries):
            try:
                chunks, _ = retrieve(entry.question, entry.history)
                if not chunks:
                    continue
                measurement = measure_stream(
                    client, model, entry.question, chunks, entry.history,
                    system_prompt=system_prompt,
                )
                measurements.append(measurement)
                if verbose:
                    print(
                        f"  [{model}] {entry.id}: ttft {measurement['ttft_ms']}ms, "
                        f"total {measurement['total_ms']}ms, {measurement['chars']} chars",
                        file=sys.stderr,
                    )
            except Exception as exc:  # one bad request should not kill the probe
                print(f"  [{model}] {entry.id}: {type(exc).__name__}: {exc}", file=sys.stderr)
        results["per_model"][model] = {
            **aggregate_latencies(measurements),
            "measurements": measurements,
        }
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure answer latency and TTFT per model")
    parser.add_argument("--models", nargs="+", default=["claude-sonnet-4-6", "claude-haiku-5-5"])
    parser.add_argument("--sample", type=int, default=10, help="Questions to measure (default 10)")
    parser.add_argument("--dataset", type=str, default=None)
    parser.add_argument("--system-prompt-file", type=str, default=None)
    parser.add_argument("--output", type=str, default=None, help="Write raw results as JSON")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    dataset = GoldenDataset(Path(args.dataset) if args.dataset else None)
    prompt = (
        Path(args.system_prompt_file).read_text(encoding="utf-8")
        if args.system_prompt_file
        else None
    )
    results = run_latency_probe(
        dataset, args.models, sample=args.sample, system_prompt=prompt,
        verbose=not args.quiet,
    )
    if args.output:
        Path(args.output).write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
        if not args.quiet:
            print(f"Results written to {args.output}", file=sys.stderr)
    if not args.quiet:
        print(json.dumps({m: {k: v for k, v in d.items() if k != "measurements"}
                          for m, d in results["per_model"].items()}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
