from dotenv import load_dotenv
load_dotenv()
from rag import retrieve, llm

SYSTEM_PROMPT = """You are a documentation assistant for Pure Data (Pd), the visual programming language for audio and multimedia.

You have access to two types of documentation:

1. The MSP Manual (msp.ucsd.edu): Conceptual chapters explaining how Pd works — message passing, audio signals, scheduling, subpatches, data structures.

2. The IEM Object Reference (pd.iem.sh): Technical specifications for individual Pd objects — inlets, outlets, creation arguments, flags, and short descriptions.

When answering:
- For questions about specific objects, use the object reference as the primary source. Always mention the object's inlets, outlets, and arguments if they're relevant to the question.
- For conceptual questions about how Pd works, use the manual as the primary source.
- When both are relevant, use the manual to explain the concept and the object reference for the specific technical details.
- Always say which source your answer comes from and cite the section name or object name.
- If the answer is not in the provided documentation, say so clearly. Do not draw on general knowledge about Pd that isn't in the excerpts."""

def generate_answer(question, context_chunks, history=None):
    if history is None:
        history = []
    # Group chunks by source for clearer context presentation
    manual_chunks = [c for c in context_chunks if c["source"] == "msp_manual"]
    object_chunks = [c for c in context_chunks if c["source"] == "iem_reference"]

    context_parts = []
    if object_chunks:
        context_parts.append("=== IEM Object Reference ===")
        for c in object_chunks:
            context_parts.append(f"[{c['object_name']}]\n{c['text']}")
    if manual_chunks:
        context_parts.append("=== MSP Manual ===")
        for c in manual_chunks:
            context_parts.append(f"[{c['heading_path']}]\n{c['text']}")

    context_text = "\n\n".join(context_parts)

    messages = history + [{
        "role": "user",
        "content": f"Documentation:\n\n{context_text}\n\nQuestion: {question}"
    }]

    response = llm.messages.create(
        model="claude-opus-4-6",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=messages
    )
    return response.content[0].text

def chat(question, history=None):
    if history is None:
        history = []
    context_chunks, classification = retrieve(question, history)
    answer = generate_answer(question, context_chunks, history)

    new_history = history + [
        {"role": "user", "content": question},
        {"role": "assistant", "content": answer}
    ]
    return answer, context_chunks, classification, new_history

if __name__ == "__main__":
    test_questions = [
        "What does osc~ do?",
        "How does message passing work in Pd?",
        "How do I use tabread4~ to implement wavetable synthesis?",
        "What are the inlets of pack?",
        "What's the difference between hot and cold inlets?"
    ]
    history = []
    for q in test_questions:
        print(f"\nQ: {q}")
        answer, chunks, classification, history = chat(q, history)
        print(f"Type: {classification['query_type']}")
        print(f"Sources: {[c['heading_path'] for c in chunks]}")
        print(f"A: {answer[:300]}...")
