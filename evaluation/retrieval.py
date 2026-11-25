"""Retrieval metrics: recall@k, MRR, nDCG with ablation variants.

Runs the full retrieval pipeline and also vector-only / BM25-only baselines
to prove hybrid RRF beats either alone. With ``agent=True`` it adds a fourth
strategy — what the agent's own ``search_docs`` tool calls retrieve — so the
report can show whether letting the model write the query helps or hurts.
"""

import asyncio
import math
from typing import Any

from .dataset import GoldenDataset, GoldenEntry
from rag import (
    retrieve,
    vector_search,
    bm25_search,
    child_chunks,
    parent_lookup,
    collection,
    HistoryItem,
    Chunk,
)


def _child_to_parent_chunks(child_ids: list[str], dedupe: bool = True) -> list[Chunk]:
    """Dereference child IDs to unique parent chunks, preserving input order."""
    if not child_ids:
        return []
    batch = collection.get(ids=child_ids, include=["metadatas"])
    id_to_meta = dict(zip(batch["ids"], batch["metadatas"]))
    seen: set[str] = set()
    results: list[Chunk] = []
    for child_id in child_ids:
        meta = id_to_meta.get(child_id)
        if meta is None:
            continue
        pid = meta["parent_id"]
        if dedupe and pid in seen:
            continue
        seen.add(pid)
        parent = parent_lookup.get(pid)
        if parent:
            results.append(parent)
    return results


def _compute_recall_at_k(
    retrieved_ids: list[str], relevant_ids: set[str], k: int
) -> float:
    top_k = set(retrieved_ids[:k])
    if not relevant_ids:
        return 0.0
    return len(top_k & relevant_ids) / len(relevant_ids)


def _compute_mrr(
    retrieved_ids: list[str], relevant_ids: set[str]
) -> float:
    for rank, rid in enumerate(retrieved_ids, start=1):
        if rid in relevant_ids:
            return 1.0 / rank
    return 0.0


def _compute_ndcg_at_k(
    retrieved_ids: list[str], relevant_ids: set[str], k: int
) -> float:
    """Binary relevance nDCG: 1 if relevant, 0 if not."""
    top_k = retrieved_ids[:k]
    dcg = 0.0
    for i, rid in enumerate(top_k):
        if rid in relevant_ids:
            dcg += 1.0 / math.log2(i + 2)  # i+2 because log2(rank+1) where rank starts at 1
    # ideal DCG: all relevant items first
    ideal = sum(1.0 / math.log2(i + 2) for i in range(min(len(relevant_ids), k)))
    return dcg / ideal if ideal > 0 else 0.0


def _parent_ids_from_chunks(chunks: list[Chunk]) -> list[str]:
    return [c["id"] for c in chunks]


class RetrievalResult:
    __slots__ = ("entry_id", "retrieved_ids", "relevant_ids", "ks")

    def __init__(
        self,
        entry_id: str,
        retrieved_ids: list[str],
        relevant_ids: set[str],
        ks: tuple[int, ...] = (3, 5, 10),
    ):
        self.entry_id = entry_id
        self.retrieved_ids = retrieved_ids
        self.relevant_ids = relevant_ids
        self.ks = ks

    @property
    def recall(self) -> dict[int, float]:
        return {k: _compute_recall_at_k(self.retrieved_ids, self.relevant_ids, k) for k in self.ks}

    @property
    def mrr(self) -> float:
        return _compute_mrr(self.retrieved_ids, self.relevant_ids)

    @property
    def ndcg(self) -> dict[int, float]:
        return {k: _compute_ndcg_at_k(self.retrieved_ids, self.relevant_ids, k) for k in self.ks}


def _run_hybrid(entry: GoldenEntry) -> RetrievalResult:
    # Route with the dataset's curated query_type rather than the live classifier
    # so this gate measures the retriever, not the router's run-to-run sampling.
    # Production routing is exercised by the scope eval and the generation eval.
    classification = {
        "rewritten_query": entry.question,
        "query_type": entry.query_type,
        "object_names": [],
        "scope": "in_scope",
        "off_topic_part": "",
        "pd_query": entry.question,
    }
    chunks, _ = retrieve(entry.question, entry.history, classification=classification)
    return RetrievalResult(
        entry.id,
        _parent_ids_from_chunks(chunks),
        set(entry.relevant_chunk_ids),
    )


def _run_vector_only(entry: GoldenEntry) -> RetrievalResult:
    from voyageai import Client as VoyageClient
    vc_local = VoyageClient()
    emb = vc_local.embed([entry.question], model="voyage-3-lite").embeddings[0]
    child_ids, metas = vector_search(entry.question, top_k=10, query_vector=emb)
    chunks = _child_to_parent_chunks(child_ids, dedupe=True)
    return RetrievalResult(
        entry.id,
        _parent_ids_from_chunks(chunks),
        set(entry.relevant_chunk_ids),
    )


def _run_bm25_only(entry: GoldenEntry) -> RetrievalResult:
    child_ids = bm25_search(entry.question, top_k=10)
    chunks = _child_to_parent_chunks(child_ids, dedupe=True)
    return RetrievalResult(
        entry.id,
        _parent_ids_from_chunks(chunks),
        set(entry.relevant_chunk_ids),
    )


def _run_agent_tool(entry: GoldenEntry) -> RetrievalResult:
    """What the agent's search_docs tool calls retrieve for this question.

    The run is capped at one turn with no forced final answer: the model gets to
    call its tools, and the chunks it asked for are scored. That measures the
    model's query choice without paying for a full answer per entry.
    """
    from agent.chat import answer_question, default_config

    result = asyncio.run(answer_question(
        entry.question,
        entry.history,
        config=default_config(execution=False, max_turns=1, force_final_answer=False),
        execution=False,
    ))
    return RetrievalResult(
        entry.id,
        _parent_ids_from_chunks(list(result.chunks)),
        set(entry.relevant_chunk_ids),
    )


def _only_entries_with_relevant(dataset: GoldenDataset) -> list[GoldenEntry]:
    return [e for e in dataset.entries if e.relevant_chunk_ids]


def _aggregate(results: list[RetrievalResult]) -> dict[str, Any]:
    if not results:
        return {}
    ks = (3, 5, 10)
    recall = {k: sum(r.recall[k] for r in results) / len(results) for k in ks}
    mrr = sum(r.mrr for r in results) / len(results)
    ndcg = {k: sum(r.ndcg[k] for r in results) / len(results) for k in ks}
    return {"recall": {f"recall@{k}": round(recall[k], 4) for k in ks},
            "mrr": round(mrr, 4),
            "ndcg": {f"ndcg@{k}": round(ndcg[k], 4) for k in ks},
            "n": len(results)}


def run_retrieval_eval(
    dataset: GoldenDataset,
    ablation: bool = True,
    verbose: bool = False,
    agent: bool = False,
) -> dict[str, Any]:
    """Run retrieval evaluation on the golden dataset.

    Returns a dict with scores for hybrid (current default) and optionally
    vector-only and BM25-only baselines, plus the agent's tool-call strategy
    when ``agent`` is set.
    """
    entries = _only_entries_with_relevant(dataset)
    if not entries:
        return {"error": "No entries with relevant_chunk_ids in dataset"}

    output: dict[str, Any] = {
        "dataset": dataset.stats,
        "ablation": ablation,
    }

    strategies: dict[str, Any] = {
        "hybrid_rrf": _run_hybrid,
    }
    if ablation:
        strategies["vector_only"] = _run_vector_only
        strategies["bm25_only"] = _run_bm25_only
    if agent:
        strategies["agent_tool"] = _run_agent_tool

    for name, runner in strategies.items():
        results: list[RetrievalResult] = []
        for entry in entries:
            try:
                r = runner(entry)
                results.append(r)
                if verbose and not r.relevant_ids:
                    print(f"  [{name}] {entry.id}: NO relevant chunks, skipping")
            except Exception as exc:
                if verbose:
                    print(f"  [{name}] {entry.id}: ERROR — {exc}")
        output[name] = _aggregate(results)

        if verbose:
            agg = output[name]
            print(f"\n--- {name} (n={agg.get('n', 0)}) ---")
            if "recall" in agg:
                for metric, val in agg["recall"].items():
                    print(f"  {metric}: {val:.4f}")
                print(f"  MRR: {agg['mrr']:.4f}")
                for metric, val in agg.get("ndcg", {}).items():
                    print(f"  {metric}: {val:.4f}")

    return output
