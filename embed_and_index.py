# embed_and_index.py
import json
import logging
import time
from dotenv import load_dotenv
load_dotenv()
import voyageai
import chromadb

logger = logging.getLogger(__name__)

vc = voyageai.Client()


def embed_with_retry(texts: list[str], model: str, max_retries: int = 5) -> list:
    for attempt in range(max_retries):
        try:
            return vc.embed(texts, model=model).embeddings
        except voyageai.error.RateLimitError as e:
            wait = 30 * (attempt + 1)
            logger.warning("Rate limited, waiting %ds... (%s)", wait, e)
            time.sleep(wait)
    raise RuntimeError("Embedding failed after max retries")


def run() -> None:
    with open("child_chunks.json") as f:
        child_chunks = json.load(f)
    with open("parent_chunks.json") as f:
        parent_chunks = json.load(f)

    parent_lookup = {p["id"]: p for p in parent_chunks}

    db = chromadb.PersistentClient(path="./chroma_db")
    try:
        db.delete_collection("pd_docs")
        logger.info("Deleted existing pd_docs collection")
    except chromadb.errors.NotFoundError:
        pass  # collection didn't exist yet
    collection = db.create_collection(
        name="pd_docs",
        metadata={"hnsw:space": "cosine"}
    )

    texts = [chunk["text"] for chunk in child_chunks]
    logger.info("Embedding %d chunks with voyage-3-lite...", len(texts))

    batch_size = 128
    embeddings = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        embeddings.extend(embed_with_retry(batch, model="voyage-3-lite"))
        logger.info("  %d/%d", min(i + batch_size, len(texts)), len(texts))

    collection.upsert(
        ids=[chunk["id"] for chunk in child_chunks],
        embeddings=embeddings,
        documents=texts,
        metadatas=[{
            "parent_id": chunk["parent_id"],
            "source": chunk["source"],
            "content_type": chunk["content_type"],
            "object_name": chunk["object_name"] or "",
            "heading_path": chunk["heading_path"],
            "url": chunk["url"]
        } for chunk in child_chunks]
    )

    logger.info("Indexed %d chunks", len(child_chunks))
    with open("parent_lookup.json", "w") as f:
        json.dump(parent_lookup, f, indent=2)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    run()
