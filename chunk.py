# chunk.py
import json, sys

def log(msg):
    print(msg, flush=True)

def split_text(text, max_chars=600, overlap_chars=100):
    """Split text into overlapping chunks, preferring sentence boundaries."""
    if len(text) <= max_chars:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = start + max_chars
        chunk = text[start:end]
        if end < len(text):
            last_period = chunk.rfind(". ")
            if last_period > max_chars // 2:
                chunk = chunk[:last_period + 1]
        chunks.append(chunk.strip())
        advance = len(chunk) - overlap_chars
        if advance <= 0:
            break
        start += advance
    return chunks

def build_all_chunks(msp_sections, iem_objects, book_sections):
    child_chunks = []
    parent_chunks = []

    # --- MSP Manual: parent-child chunking ---
    for section in msp_sections:
        # Prepend heading path to text before embedding
        # This improves retrieval for navigational queries like
        # "how do hot and cold inlets work" — the heading path
        # "Messages > Hot and Cold Inlets" is part of what gets embedded
        full_text = f"{section['heading_path']}\n\n{section['text']}"

        parent_id = f"msp_parent_{len(parent_chunks)}"
        parent_chunks.append({
            "id": parent_id,
            "text": full_text,
            "source": "msp_manual",
            "content_type": "conceptual",
            "object_name": None,
            "heading_path": section["heading_path"],
            "url": section["url"]
        })

        child_texts = split_text(full_text)
        for child_text in child_texts:
            child_chunks.append({
                "id": f"msp_child_{len(child_chunks)}",
                "parent_id": parent_id,
                "text": child_text,
                "source": "msp_manual",
                "content_type": "conceptual",
                "object_name": None,
                "heading_path": section["heading_path"],
                "url": section["url"]
            })

    # --- IEM Objects: atomic for short entries, split for long ones ---
    for idx, obj in enumerate(iem_objects):
        parent_id = f"iem_parent_{idx}"
        full_text = obj["text"]
        base = {
            "source": "iem_reference",
            "content_type": "object_reference",
            "object_name": obj["object_name"],
            "heading_path": obj["object_name"],
            "url": obj["url"],
        }

        parent_chunks.append({"id": parent_id, "parent_id": parent_id, "text": full_text, **base})

        child_texts = split_text(full_text)
        if len(child_texts) == 1:
            # Short object: single child points to itself as parent
            child_chunks.append({"id": parent_id, "parent_id": parent_id, "text": full_text, **base})
        else:
            # Long object: each child prefixed with "Object: name" for context
            prefix = f"Object: {obj['object_name']}\n\n"
            for child_text in child_texts:
                child_chunks.append({
                    "id": f"iem_child_{len(child_chunks)}",
                    "parent_id": parent_id,
                    "text": prefix + child_text,
                    **base,
                })

    # --- Puckette book: same parent-child chunking as MSP manual ---
    for section in book_sections:
        full_text = f"{section['heading_path']}\n\n{section['text']}"

        parent_id = f"book_parent_{len(parent_chunks)}"
        parent_chunks.append({
            "id": parent_id,
            "text": full_text,
            "source": "puckette_book",
            "content_type": "conceptual",
            "object_name": None,
            "heading_path": section["heading_path"],
            "url": section["url"]
        })

        child_texts = split_text(full_text)
        for child_text in child_texts:
            child_chunks.append({
                "id": f"book_child_{len(child_chunks)}",
                "parent_id": parent_id,
                "text": child_text,
                "source": "puckette_book",
                "content_type": "conceptual",
                "object_name": None,
                "heading_path": section["heading_path"],
                "url": section["url"]
            })

    return child_chunks, parent_chunks

log("Loading parsed_manual.json...")
with open("parsed_manual.json") as f:
    msp_sections = json.load(f)
log(f"  {len(msp_sections)} sections")

log("Loading parsed_object_reference.json...")
with open("parsed_object_reference.json") as f:
    iem_objects = json.load(f)
log(f"  {len(iem_objects)} objects")

log("Loading parsed_book.json...")
with open("parsed_book.json") as f:
    book_sections = json.load(f)
log(f"  {len(book_sections)} sections")

log("Building chunks...")
child_chunks, parent_chunks = build_all_chunks(msp_sections, iem_objects, book_sections)
log(f"  {len(child_chunks)} child chunks, {len(parent_chunks)} parent chunks")

log("Writing child_chunks.json...")
with open("child_chunks.json", "w") as f:
    json.dump(child_chunks, f, indent=2)

log("Writing parent_chunks.json...")
with open("parent_chunks.json", "w") as f:
    json.dump(parent_chunks, f, indent=2)

log(f"\nDone.")
log(f"  MSP manual chunks: {sum(1 for c in child_chunks if c['source'] == 'msp_manual')}")
log(f"  IEM object entries: {sum(1 for c in child_chunks if c['source'] == 'iem_reference')}")
log(f"  Puckette book chunks: {sum(1 for c in child_chunks if c['source'] == 'puckette_book')}")
