# Deferred Improvements

Items identified but not yet implemented. Ordered by effort/impact.

---

## Security

### Move API key out of the browser bundle
`VITE_CHAT_API_KEY` is baked into the static JS at build time — anyone who inspects the bundle can extract it and call Railway directly.

**Recommended fix**: Replace the `vercel.json` rewrite rule with a Vercel Edge Function that injects the `X-API-Key` header server-side. The browser JS never sees the key.

```typescript
// api/[...path].ts  (Vercel Edge Function)
export const config = { runtime: 'edge' }

export default async function handler(req: Request) {
  const url = req.url.replace(process.env.VERCEL_URL, process.env.RAILWAY_BACKEND_URL)
  return fetch(url, {
    method: req.method,
    headers: {
      ...Object.fromEntries(req.headers),
      'X-API-Key': process.env.CHAT_API_KEY,
    },
    body: req.body,
  })
}
```

Remove `VITE_CHAT_API_KEY` from the frontend env and the `useChat.ts` fetch call.

---

### Accept multiple CORS origins
`ALLOWED_ORIGIN` is a single string. Adding a preview URL or staging environment requires a code change. Accept a comma-separated list:

```python
# main.py
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGIN", "").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, ...)
```

---

## Scalability

### Redis-backed response cache
`TTLCache` is in-process memory. Each Railway instance has its own cold cache; restarts and horizontal scaling lose all cached responses.

Replace with Redis using `redis-py`. The cache key structure and TTL semantics are unchanged:

```python
import redis, json

r = redis.from_url(os.environ["REDIS_URL"])
TTL = 3600

def cache_get(key):
    val = r.get(key)
    return json.loads(val) if val else None

def cache_set(key, value):
    r.setex(key, TTL, json.dumps(value))
```

Railway has a Redis add-on available from the dashboard.

---

### Persist ChromaDB across deploys
`embed_and_index.py` runs on every Railway deploy, re-embedding all 2,503 chunks (~90s, ~$0.005). Cost and time scale linearly with corpus size.

Fix: mount a Railway **Volume** at `./chroma_db` and skip re-embedding when the chunk files haven't changed:

```python
# embed_and_index.py
import hashlib, json, pathlib

FINGERPRINT_FILE = pathlib.Path("./chroma_db/.chunk_fingerprint")
current = hashlib.sha256(open("child_chunks.json", "rb").read()).hexdigest()

if FINGERPRINT_FILE.exists() and FINGERPRINT_FILE.read_text() == current:
    print("Chunks unchanged — skipping re-embed")
    exit(0)

# ... run embed logic ...

FINGERPRINT_FILE.write_text(current)
```

---

### Managed vector database (high scale)
ChromaDB in embedded mode (`PersistentClient`) cannot be shared across multiple uvicorn workers or Railway instances. At higher traffic, replace with a managed vector DB:

- **Pinecone** or **Qdrant Cloud** — both have free tiers and clean Python clients
- Swap is isolated to `vector_search()` — only the `collection.query()` call and `vc.embed()` setup need to change

The rest of the retrieval pipeline (BM25, RRF, parent lookup) is unchanged.
