"""Documentation retrieval: hybrid search, parent lookup and relevance gate.

This is the retrieval *capability*, independent of how a caller decides to use
it. Two callers share it:

- the fixed pipeline (``rag.retrieve``), which classifies the question first and
  then routes it to the right corpus;
- the agent's ``search_docs`` tool (``agent.tools.docs``), which lets the model
  choose the query, the corpus and whether to search again.

Search is parent-level: Chroma indexes 600-character child chunks, and the RRF
ranking maps the winning children back to their full parent section (see
``parent_lookup``) so the model gets the whole section rather than a fragment.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
import chromadb
import newrelic.agent
import voyageai
from rank_bm25 import BM25Okapi

import scope
from .types import Chunk, Source

logger = logging.getLogger(__name__)

# --- Clients and indexes (initialized once at startup) ---
vc = voyageai.Client()
db = chromadb.PersistentClient(path="./chroma_db")
collection = db.get_collection("pd_docs")

with open("child_chunks.json") as f:
    child_chunks: list[Chunk] = json.load(f)
with open("parent_lookup.json") as f:
    parent_lookup: dict[str, Chunk] = json.load(f)

# Build BM25 index over all child chunks for keyword search
tokenized_corpus = [c["text"].lower().split() for c in child_chunks]
bm25 = BM25Okapi(tokenized_corpus)

_OBJECT_IN_BRACKETS = re.compile(r"\[([A-Za-z0-9_~.+\-]{1,40})(?:\s[^\]]*)?\]")


def object_names_from_question(question: str) -> list[str]:
    """Object names written as [osc~] in the question itself.

    Deterministic companion to the classifier's object_names: the exact-object
    lookup is the part of hybrid retrieval that beats vector-only, and relying on
    an LLM to re-extract names the user already wrote in brackets made recall
    swing between runs.
    """
    names: list[str] = []
    for match in _OBJECT_IN_BRACKETS.finditer(question):
        name = match.group(1).strip()
        if name and name not in names:
            names.append(name)
    return names


def vector_search(
    query: str,
    top_k: int = 10,
    content_type: str | None = None,
    object_name: str | None = None,
    query_vector: list[float] | None = None,
) -> tuple[list[str], list[dict]]:
    """Search ChromaDB with optional metadata filters."""
    if query_vector is None:
        with newrelic.agent.ExternalTrace('voyageai', 'api.voyageai.com', 'POST'):
            query_vector = vc.embed([query], model="voyage-3-lite").embeddings[0]

    filters = []
    if content_type:
        filters.append({"content_type": {"$eq": content_type}})
    if object_name:
        filters.append({"object_name": {"$eq": object_name}})
    if len(filters) == 1:
        where = filters[0]
    elif len(filters) > 1:
        where = {"$and": filters}
    else:
        where = None

    results = collection.query(
        query_embeddings=[query_vector],
        n_results=top_k,
        where=where,
        include=["metadatas", "distances"]
    )
    return results["ids"][0], results["metadatas"][0]


def max_similarity(
    query: str,
    *,
    query_vector: list[float] | None = None,
    content_type: str | None = None,
    n_results: int = 5,
) -> float | None:
    """Best cosine similarity among the top matches, or None when nothing matches.

    Chroma is configured with ``hnsw:space=cosine``, so it returns ``1 - cosine
    similarity`` as the distance. This is what makes a relevance cutoff possible:
    the hybrid retriever ranks by RRF and throws the distances away, so a query
    with no real match still comes back with its five least-bad chunks.
    """
    if query_vector is None:
        with newrelic.agent.ExternalTrace('voyageai', 'api.voyageai.com', 'POST'):
            query_vector = vc.embed([query], model="voyage-3-lite").embeddings[0]
    where = {"content_type": {"$eq": content_type}} if content_type else None
    results = collection.query(
        query_embeddings=[query_vector],
        n_results=n_results,
        where=where,
        include=["distances"],
    )
    distances = (results.get("distances") or [[]])[0]
    if not distances:
        return None
    return 1.0 - min(distances)


def bm25_search(query: str, top_k: int = 10, content_type: str | None = None) -> list[str]:
    """BM25 keyword search — especially good for exact Pd object names."""
    tokenized_query = query.lower().split()
    scores = bm25.get_scores(tokenized_query)

    filtered = [
        (i, scores[i]) for i, c in enumerate(child_chunks)
        if content_type is None or c["content_type"] == content_type
    ]
    filtered.sort(key=lambda x: x[1], reverse=True)
    top_indices = [i for i, _ in filtered[:top_k]]
    return [child_chunks[i]["id"] for i in top_indices]


def hybrid_retrieve(
    query: str,
    top_k: int = 5,
    content_type: str | None = None,
    object_names: list[str] | None = None,
    bm25_query: str | None = None,
    query_vector: list[float] | None = None,
) -> list[Chunk]:
    """
    Combine vector search and BM25 via RRF, return top-k by rank.
    If specific object names are mentioned, also do exact lookups.
    bm25_query defaults to query but can be set to the original user question
    to preserve exact object name tokens (e.g. osc~) that a rewritten query may drop.
    query_vector may be passed in from retrieve() to avoid redundant Voyage AI calls.
    """
    if bm25_query is None:
        bm25_query = query

    # If a specific object name is mentioned, always include its exact entry
    exact_results = []
    if object_names:
        for name in object_names:
            exact_ids, exact_metas = vector_search(
                query, top_k=1,
                content_type="object_reference",
                object_name=name,
                query_vector=query_vector,
            )
            for id_, meta in zip(exact_ids, exact_metas):
                parent = parent_lookup.get(meta["parent_id"])
                if parent:
                    exact_results.append(parent)

    # Broad vector + BM25 search
    v_ids, v_metas = vector_search(query, top_k=top_k * 2, content_type=content_type, query_vector=query_vector)
    k_ids = bm25_search(bm25_query, top_k=top_k * 2, content_type=content_type)

    # Reciprocal rank fusion
    combined = {}
    for rank, id_ in enumerate(v_ids):
        combined[id_] = combined.get(id_, 0) + 0.5 / (rank + 1)
    for rank, id_ in enumerate(k_ids):
        combined[id_] = combined.get(id_, 0) + 0.5 / (rank + 1)

    sorted_ids = sorted(combined, key=combined.get, reverse=True)

    # Batch-fetch all child metadata in one ChromaDB call (avoids N+1).
    # Build a dict first so we can iterate in RRF order — collection.get()
    # returns results in internal storage order, not in the requested id order.
    seen = set(r["id"] for r in exact_results)
    candidates = list(exact_results)

    if sorted_ids:
        batch = collection.get(ids=sorted_ids, include=["metadatas"])
        id_to_meta = dict(zip(batch["ids"], batch["metadatas"]))
        for id_ in sorted_ids:
            meta = id_to_meta.get(id_)
            if meta is None:
                continue
            parent_id = meta["parent_id"]
            if parent_id not in seen:
                seen.add(parent_id)
                parent = parent_lookup.get(parent_id)
                if parent:
                    candidates.append(parent)
            if len(candidates) >= top_k * 2:
                break

    if not candidates:
        return []

    return candidates[:top_k]


@dataclass(frozen=True)
class RetrievalOutcome:
    """What one search produced, including why it produced nothing."""

    chunks: list[Chunk] = field(default_factory=list)
    query: str = ""
    query_type: str | None = None
    best_similarity: float | None = None
    below_cutoff: bool = False


def search_documents(
    query: str,
    *,
    top_k: int = 5,
    query_type: str | None = None,
    content_type: str | None = None,
    object_names: list[str] | None = None,
    bm25_query: str | None = None,
    apply_relevance_cutoff: bool = True,
) -> RetrievalOutcome:
    """Retrieve parent sections for a query.

    ``query_type`` mirrors the pipeline's routing: object_reference searches the
    object corpus, conceptual the manual, and "both" interleaves the two. When it
    is None the caller has already chosen a corpus via ``content_type`` (or wants
    everything), which is how the agent's tool passes an explicit filter.

    The relevance backstop is applied here rather than in the callers so a query
    with no real match returns nothing in both modes: hybrid retrieval always
    returns its top-k, so without the cutoff an unrelated query would still be
    answered from five irrelevant chunks.
    """
    if bm25_query is None:
        bm25_query = query

    with newrelic.agent.ExternalTrace('voyageai', 'api.voyageai.com', 'POST'):
        query_vector = vc.embed([query], model="voyage-3-lite").embeddings[0]

    if query_type == "object_reference":
        chunks = hybrid_retrieve(
            query,
            top_k=top_k,
            content_type="object_reference",
            object_names=object_names,
            bm25_query=bm25_query,
            query_vector=query_vector,
        )
    elif query_type == "conceptual":
        chunks = hybrid_retrieve(
            query,
            top_k=top_k,
            content_type="conceptual",
            bm25_query=bm25_query,
            query_vector=query_vector,
        )
    elif query_type == "both":
        obj_results = hybrid_retrieve(
            query,
            top_k=3,
            content_type="object_reference",
            object_names=object_names,
            bm25_query=bm25_query,
            query_vector=query_vector,
        )
        conceptual_results = hybrid_retrieve(
            query,
            top_k=3,
            content_type="conceptual",
            bm25_query=bm25_query,
            query_vector=query_vector,
        )
        # Interleave the two sources instead of concatenating them: recall@5
        # scores the first five chunks, and putting all three object results
        # first buried the conceptual match a mixed question needed.
        chunks = []
        for obj, con in zip(obj_results, conceptual_results):
            chunks.extend([obj, con])
        if len(obj_results) > len(conceptual_results):
            chunks.extend(obj_results[len(conceptual_results):])
        elif len(conceptual_results) > len(obj_results):
            chunks.extend(conceptual_results[len(obj_results):])
    else:
        chunks = hybrid_retrieve(
            query,
            top_k=top_k,
            content_type=content_type,
            object_names=object_names,
            bm25_query=bm25_query,
            query_vector=query_vector,
        )

    best_similarity: float | None = None
    if chunks and apply_relevance_cutoff:
        best_similarity = max_similarity(query, query_vector=query_vector, content_type=None)
    below_cutoff = (
        best_similarity is not None
        and best_similarity < scope.MIN_RELEVANCE_SIMILARITY
    )
    if below_cutoff:
        logger.info(
            "low relevance (best similarity %.3f < %.3f) q=%r",
            best_similarity, scope.MIN_RELEVANCE_SIMILARITY, query[:80],
        )
        newrelic.agent.record_custom_metric("Custom/Guard/LowRelevance", 1)
        chunks = []

    return RetrievalOutcome(
        chunks=chunks,
        query=query,
        query_type=query_type,
        best_similarity=best_similarity,
        below_cutoff=below_cutoff,
    )


#: How each corpus source is named in the excerpt block the model cites from.
#: The book used to fall through to "MSP Manual"; the file-format spec is
#: unofficial and for Pd 0.37, which its label says (TASK-5.41).
SOURCE_LABELS = {
    "msp_manual": "MSP Manual",
    "iem_reference": "IEM Object Reference",
    "puckette_book": "Puckette, Theory and Technique of Electronic Music",
    "pd_file_format": "Unofficial Pd 0.37 file format specification",
}


def format_excerpts(chunks: list[Chunk]) -> str:
    """Render parent chunks as the citable excerpt block the model reads."""
    parts = []
    for chunk in chunks:
        source = chunk.get("source", "")
        url = chunk.get("url", "")
        heading = chunk.get("heading_path", "")
        text = chunk.get("text", "")
        label = SOURCE_LABELS.get(source, "MSP Manual")
        parts.append(f"[{label}: {heading}]\n{text}\nSource: {url}")
    return "\n\n---\n\n".join(parts)


def chunks_to_sources(chunks: list[Chunk]) -> list[Source]:
    return [
        {
            "heading_path": c.get("heading_path", ""),
            "url": c.get("url", ""),
            "source": c.get("source", ""),
            "content_type": c.get("content_type", ""),
            "object_name": c.get("object_name"),
        }
        for c in chunks
    ]
