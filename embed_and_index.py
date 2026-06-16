# embed_and_index.py
import json
import chromadb
from sentence_transformers import SentenceTransformer

embedder = SentenceTransformer("all-MiniLM-L6-v2")

with open("child_chunks.json") as f:
    child_chunks = json.load(f)
with open("parent_chunks.json") as f:
    parent_chunks = json.load(f)

parent_lookup = {p["id"]: p for p in parent_chunks}

db = chromadb.PersistentClient(path="./chroma_db")
collection = db.get_or_create_collection(
    name="pd_docs",
    metadata={"hnsw:space": "cosine"}
)

texts = [chunk["text"] for chunk in child_chunks]
print(f"Embedding {len(texts)} chunks...")
embeddings = embedder.encode(texts, batch_size=32, show_progress_bar=True)

collection.upsert(
    ids=[chunk["id"] for chunk in child_chunks],
    embeddings=embeddings.tolist(),
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

print(f"Indexed {len(child_chunks)} chunks")
with open("parent_lookup.json", "w") as f:
    json.dump(parent_lookup, f, indent=2)
