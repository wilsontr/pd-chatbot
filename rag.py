# rag.py
import json
import chromadb
from dotenv import load_dotenv
load_dotenv()
import voyageai
from rank_bm25 import BM25Okapi
import anthropic

vc = voyageai.Client()
db = chromadb.PersistentClient(path="./chroma_db")
collection = db.get_collection("pd_docs")
llm = anthropic.Anthropic()

with open("child_chunks.json") as f:
    child_chunks = json.load(f)
with open("parent_lookup.json") as f:
    parent_lookup = json.load(f)

# Build BM25 index over all child chunks for keyword search
tokenized_corpus = [c["text"].lower().split() for c in child_chunks]
bm25 = BM25Okapi(tokenized_corpus)


def classify_query(question, history):
    """
    Classify the query type and extract object names.
    Also rewrites the question as a standalone search query
    if there is conversation history that provides context.
    """
    history_text = ""
    if history:
        recent = history[-4:]
        history_text = "\n".join([
            f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content'][:200]}"
            for m in recent
        ])

    prompt = f"""You are helping route questions about Pure Data (Pd) to the right documentation source.

{"Conversation history:\n" + history_text if history_text else ""}

Question: {question}

Respond in JSON with these fields:
- "rewritten_query": standalone version of the question incorporating any context from history
- "query_type": one of "object_reference", "conceptual", or "both"
- "object_names": list of Pd object names mentioned (e.g. ["osc~", "pack"]), empty list if none

Return only the JSON object, no other text."""

    response = llm.messages.create(
        model="claude-haiku-4-5",
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}]
    )
    try:
        return json.loads(response.content[0].text.strip())
    except json.JSONDecodeError:
        return {
            "rewritten_query": question,
            "query_type": "both",
            "object_names": []
        }


def vector_search(query, top_k=10, content_type=None, object_name=None):
    """Search ChromaDB with optional metadata filters."""
    query_vector = vc.embed([query], model="voyage-3-lite").embeddings[0]

    where = {}
    if content_type:
        where["content_type"] = {"$eq": content_type}
    if object_name:
        where["object_name"] = {"$eq": object_name}

    results = collection.query(
        query_embeddings=[query_vector],
        n_results=top_k,
        where=where if where else None,
        include=["metadatas", "distances"]
    )
    return results["ids"][0], results["metadatas"][0]


def bm25_search(query, top_k=10, content_type=None):
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


def hybrid_retrieve(query, top_k=5, content_type=None, object_names=None, bm25_query=None):
    """
    Combine vector search and BM25 via RRF, return top-k by rank.
    If specific object names are mentioned, also do exact lookups.
    bm25_query defaults to query but can be set to the original user question
    to preserve exact object name tokens (e.g. osc~) that a rewritten query may drop.
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
                object_name=name
            )
            for id_, meta in zip(exact_ids, exact_metas):
                parent = parent_lookup.get(meta["parent_id"])
                if parent:
                    exact_results.append(parent)

    # Broad vector + BM25 search
    v_ids, v_metas = vector_search(query, top_k=top_k * 2, content_type=content_type)
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


def retrieve(question, history=None):
    """Full retrieval pipeline: classify, then search appropriately."""
    if history is None:
        history = []
    classification = classify_query(question, history)
    query = classification["rewritten_query"]
    query_type = classification["query_type"]
    object_names = classification.get("object_names", [])

    if query_type == "object_reference":
        return hybrid_retrieve(
            query,
            content_type="object_reference",
            object_names=object_names,
            bm25_query=question
        ), classification

    elif query_type == "conceptual":
        return hybrid_retrieve(
            query,
            content_type="conceptual",
            bm25_query=question
        ), classification

    else:  # "both"
        obj_results = hybrid_retrieve(
            query,
            top_k=3,
            content_type="object_reference",
            object_names=object_names,
            bm25_query=question
        )
        conceptual_results = hybrid_retrieve(
            query,
            top_k=3,
            content_type="conceptual",
            bm25_query=question
        )
        return obj_results + conceptual_results, classification


def generate_response(question, context_chunks, history=None):
    """Generate a response using Claude with retrieved context."""
    if history is None:
        history = []

    context_parts = []
    for chunk in context_chunks:
        source = chunk.get("source", "")
        url = chunk.get("url", "")
        heading = chunk.get("heading_path", "")
        text = chunk.get("text", "")
        if source == "iem_reference":
            context_parts.append(f"[IEM Object Reference: {heading}]\n{text}\nSource: {url}")
        else:
            context_parts.append(f"[MSP Manual: {heading}]\n{text}\nSource: {url}")

    context_str = "\n\n---\n\n".join(context_parts)

    system_prompt = (
        "You are a helpful assistant that answers questions about Pure Data (Pd), "
        "a visual programming language for music and multimedia. "
        "Use the provided documentation excerpts as your primary source. "
        "You may draw on broader knowledge of Pd to explain concepts or suggest implementation "
        "strategies, but ground your answer in the documentation where it is relevant. "
        "If the documentation contradicts general knowledge, prefer the documentation. "
        "Include relevant object names and brief examples where helpful. "
        "Cite the source URLs at the end of your answer."
    )

    messages = list(history) + [{
        "role": "user",
        "content": f"Documentation excerpts:\n\n{context_str}\n\n---\n\nQuestion: {question}"
    }]

    response = llm.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=system_prompt,
        messages=messages
    )
    return response.content[0].text


def chat(question, history=None):
    """Single-turn entry point used by the API. Returns (answer, chunks, classification, new_history)."""
    if history is None:
        history = []
    context_chunks, classification = retrieve(question, history)
    if not context_chunks:
        return (
            "I couldn't find any relevant Pure Data documentation for that question. "
            "Try asking about a specific Pd object, patching concept, or audio technique.",
            [],
            classification,
            history,
        )
    answer = generate_response(question, context_chunks, history)
    new_history = history + [
        {"role": "user", "content": question},
        {"role": "assistant", "content": answer},
    ]
    return answer, context_chunks, classification, new_history


def main():
    """Interactive chat loop for the Pd documentation assistant."""
    print("Pure Data Documentation Assistant")
    print("Ask questions about Pure Data objects, patching, and concepts.")
    print("Type 'quit' or 'exit' to stop.\n")

    history = []

    while True:
        try:
            question = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not question:
            continue
        if question.lower() in ("quit", "exit"):
            print("Goodbye!")
            break

        context_chunks, classification = retrieve(question, history)

        if not context_chunks:
            print("Assistant: I couldn't find relevant documentation for that question.\n")
            continue

        answer = generate_response(question, context_chunks, history)
        print(f"\nAssistant: {answer}\n")

        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": answer})
        # Keep last 10 turns to prevent context overflow
        if len(history) > 20:
            history = history[-20:]


if __name__ == "__main__":
    main()
