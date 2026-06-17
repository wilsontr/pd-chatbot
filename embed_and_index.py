# embed_and_index.py
import json
import time
from dotenv import load_dotenv
load_dotenv()
import voyageai
import chromadb

vc = voyageai.Client()

def embed_with_retry(texts, model, max_retries=5):
    for attempt in range(max_retries):
        try:
            return vc.embed(texts, model=model).embeddings
        except voyageai.error.RateLimitError as e:
            wait = 30 * (attempt + 1)
            print(f"  Rate limited, waiting {wait}s... ({e})")
            time.sleep(wait)
    raise RuntimeError("Embedding failed after max retries")

with open("child_chunks.json") as f:
    child_chunks = json.load(f)
with open("parent_chunks.json") as f:
    parent_chunks = json.load(f)

parent_lookup = {p["id"]: p for p in parent_chunks}

db = chromadb.PersistentClient(path="./chroma_db")
try:
    db.delete_collection("pd_docs")
    print("Deleted existing pd_docs collection")
except Exception:
    pass
collection = db.create_collection(
    name="pd_docs",
    metadata={"hnsw:space": "cosine"}
)

texts = [chunk["text"] for chunk in child_chunks]
print(f"Embedding {len(texts)} chunks with voyage-3-lite...")

batch_size = 128
embeddings = []
for i in range(0, len(texts), batch_size):
    batch = texts[i:i + batch_size]
    embeddings.extend(embed_with_retry(batch, model="voyage-3-lite"))
    print(f"  {min(i + batch_size, len(texts))}/{len(texts)}")

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

print(f"Indexed {len(child_chunks)} chunks")
with open("parent_lookup.json", "w") as f:
    json.dump(parent_lookup, f, indent=2)
