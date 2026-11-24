"""The ``search_docs`` tool: documentation retrieval the model drives itself.

This is the tool that replaces the pipeline's fixed retrieval step. The model
writes the query, picks the corpus and can search again; the tool returns parent
sections in the same excerpt format the pipeline injects into its prompt, with
the same relevance cutoff, so an unrelated query still comes back empty instead
of answering from five irrelevant chunks.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..retrieval import RetrievalOutcome, format_excerpts, search_documents
from . import ToolOutcome, ToolSpec

SEARCH_DOCS_DESCRIPTION = (
    "Search the Pure Data documentation (the MSP manual and the IEM object "
    "reference) for the sections most relevant to a query. Use it before "
    "answering any question about documented Pd behaviour. Returns up to five "
    "sections, each with a heading, the section text, and a 'Source:' URL you "
    "may cite. If nothing relevant is found it says so — retry with different "
    "terms rather than answering from memory."
)

SEARCH_DOCS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": (
                "Search terms, not a conversational reply — object names and "
                "technical words, e.g. 'osc~ inlets outlets' or 'band-pass "
                "filter cutoff resonance'."
            ),
        },
        "content_type": {
            "type": "string",
            "enum": ["object_reference", "conceptual", "both"],
            "description": (
                "'object_reference' for a named object's reference page, "
                "'conceptual' for manual and technique sections, 'both' (the "
                "default) when unsure."
            ),
        },
        "object_names": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Exact Pd object names the question mentions (e.g. ['osc~', "
                "'pack']); their reference entries are fetched exactly."
            ),
        },
    },
    "required": ["query"],
    "additionalProperties": False,
}

_VALID_CONTENT_TYPES = {"object_reference", "conceptual"}


def _clean_names(raw: Any) -> list[str]:
    if not isinstance(raw, (list, tuple)):
        return []
    names: list[str] = []
    for item in raw:
        name = str(item).strip()
        if name and name not in names:
            names.append(name)
    return names


class SearchDocsTool:
    """Hybrid search over the Pd corpus, exposed to the model as a tool."""

    spec = ToolSpec(
        name="search_docs",
        description=SEARCH_DOCS_DESCRIPTION,
        input_schema=SEARCH_DOCS_SCHEMA,
    )

    def __init__(self, *, top_k: int = 5):
        self._top_k = top_k

    def run(self, arguments: Mapping[str, Any]) -> ToolOutcome:
        query = str(arguments.get("query") or "").strip()
        if not query:
            return ToolOutcome(
                content=(
                    "search_docs needs a non-empty 'query'. Call it again with the "
                    "Pd object names or technical terms you are looking for."
                ),
                summary="search_docs called without a query",
                ok=False,
            )

        raw_type = arguments.get("content_type")
        content_type = raw_type if raw_type in _VALID_CONTENT_TYPES else None
        object_names = _clean_names(arguments.get("object_names"))

        outcome: RetrievalOutcome = search_documents(
            query,
            top_k=self._top_k,
            content_type=content_type,
            object_names=object_names or None,
        )

        if not outcome.chunks:
            return ToolOutcome(
                content=(
                    "No documentation sections matched that query. Try broader or "
                    "different terms (or set content_type to 'both'), or tell the "
                    "user the documentation does not cover it. Do not invent a "
                    "source URL."
                ),
                summary=f"no documentation matched {query!r}",
                ok=True,
                data={
                    "query": query,
                    "content_type": content_type,
                    "matched": 0,
                    "best_similarity": outcome.best_similarity,
                },
            )

        return ToolOutcome(
            content=format_excerpts(outcome.chunks),
            summary=f"{len(outcome.chunks)} sections for {query!r}",
            ok=True,
            chunks=tuple(outcome.chunks),
            data={
                "query": query,
                "content_type": content_type,
                "matched": len(outcome.chunks),
                "headings": [c.get("heading_path", "")[:120] for c in outcome.chunks],
                "best_similarity": outcome.best_similarity,
                "below_relevance_cutoff": outcome.below_cutoff,
            },
        )


def default_tools() -> list[SearchDocsTool]:
    """The tool set a documentation agent run starts with."""
    return [SearchDocsTool()]
