"""Plain dict shapes shared by the pipeline and the agent core.

These are type aliases rather than classes on purpose: chunks come straight out
of JSON files and Chroma metadata, and history is whatever the client sent. The
agent core adds its own typed *events* on top (see ``agent.events``) instead of
re-wrapping the payloads.
"""

from __future__ import annotations

from typing import Any

#: One chat turn: ``{"role": "user"|"assistant", "content": str}``
HistoryItem = dict[str, str]

#: A parent chunk from ``parent_lookup.json``.
Chunk = dict[str, Any]

#: The source citation shape sent to clients: heading, url, corpus, type, object.
Source = dict[str, Any]
