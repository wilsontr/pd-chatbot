"""Retrieval metrics: recall@k, MRR, nDCG with ablation variants.

Runs the full retrieval pipeline and also vector-only / BM25-only baselines
to prove hybrid RRF beats either alone.
"""

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
    chunks, _ = retrieve(entry.question, entry.history)
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
) -> dict[str, Any]:
    """Run retrieval evaluation on the golden dataset.

    Returns a dict with scores for hybrid (current default) and optionally
    vector-only and BM25-only baselines.
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
