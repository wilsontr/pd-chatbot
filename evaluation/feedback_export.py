"""Export rated chat responses as golden-dataset candidates (TASK-2).

Downvoted answers are the most valuable source of new hard eval questions:
they are real user questions the current pipeline got wrong. Feedback rows
already carry the question, the answer and the retrieved chunks, so they can
be turned into entries shaped exactly like ``evaluation/golden_dataset*.json``
for a human to review.

The export is deliberately one-way and manual:

* it writes a separate review file (``evaluation/feedback_candidates.json``
  by default), and
* it refuses to write any ``golden_dataset*.json`` path,

so the golden set only ever grows by a hand edit. The review file is a plain
JSON list in the dataset schema, which means ``GoldenDataset`` can load it
unchanged to sanity-check what was exported.

Usage:
    python -m evaluation.export_feedback --rating down
    python -m evaluation.export_feedback --since 2026-11-01 --until 2026-11-30
    DATABASE_URL=... python -m evaluation.export_feedback --rating both

Date filters are UTC and inclusive on both ends: ``--since 2026-11-01`` starts
at 00:00 on the 1st, ``--until 2026-11-30`` ends at 24:00 on the 30th.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from evaluation.dataset import GoldenEntry
from feedback_store import FeedbackStore

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = REPO_ROOT / "evaluation" / "feedback_candidates.json"
DEFAULT_PARENT_LOOKUP = REPO_ROOT / "parent_lookup.json"

# Any file the evaluation harness would treat as the golden set.
GOLDEN_NAME_RE = re.compile(r"^golden_dataset.*\.json$", re.IGNORECASE)

VALID_QUERY_TYPES = ("object_reference", "conceptual", "both")

# A source is identified by (heading, url, source kind). Those three are unique
# across parent_lookup.json, so a stored source maps back to exactly one parent
# chunk, which is the id space `relevant_chunk_ids` uses.
SourceKey = tuple[str, str, str]


class GoldenSetWriteRefused(ValueError):
    """Raised when an export target looks like the golden dataset itself."""


def _as_utc(value: datetime) -> datetime:
    """SQLite hands back naive UTC datetimes; Postgres hands back aware ones."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_rated_at(row: Mapping[str, Any]) -> datetime | None:
    raw = row.get("rated_at")
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return _as_utc(raw)
    if isinstance(raw, date):
        return datetime.combine(raw, time.min, tzinfo=timezone.utc)
    if isinstance(raw, str):
        text = raw.strip().replace("Z", "+00:00")
        try:
            return _as_utc(datetime.fromisoformat(text))
        except ValueError:
            return None
    return None


def source_key(source: Mapping[str, Any]) -> SourceKey | None:
    heading = (source.get("heading_path") or "").strip()
    url = (source.get("url") or "").strip()
    kind = (source.get("source") or "").strip()
    if not (heading and url and kind):
        return None
    return (heading, url, kind)


def load_parent_index(path: Path | str = DEFAULT_PARENT_LOOKUP) -> dict[SourceKey, str]:
    """Map (heading, url, source) back to the parent chunk id in the golden set."""
    with open(path) as f:
        parents = json.load(f)
    index: dict[SourceKey, str] = {}
    for chunk in parents.values() if isinstance(parents, dict) else parents:
        key = source_key(chunk)
        chunk_id = chunk.get("id")
        if key and chunk_id:
            index[key] = chunk_id
    return index


def resolve_chunk_ids(
    sources: Iterable[Mapping[str, Any]], index: Mapping[SourceKey, str]
) -> tuple[list[str], list[Mapping[str, Any]]]:
    """Return the parent chunk ids for stored sources, plus the ones we could not resolve."""
    ids: list[str] = []
    unresolved: list[Mapping[str, Any]] = []
    for source in sources or []:
        chunk_id = (source.get("chunk_id") or "").strip()
        if not chunk_id:
            key = source_key(source)
            chunk_id = index.get(key, "") if key else ""
        if not chunk_id:
            unresolved.append(source)
            continue
        if chunk_id not in ids:
            ids.append(chunk_id)
    return ids, unresolved


def _describe_sources(sources: Iterable[Mapping[str, Any]]) -> str:
    labels = []
    for source in sources or []:
        kind = source.get("source") or "unknown source"
        heading = source.get("heading_path") or "untitled section"
        labels.append(f"{kind}: {heading}")
    return "; ".join(labels) if labels else "none"


def _unique_id(message_id: str, used: set[str]) -> str:
    """Stable, readable id derived from the message id, never colliding."""
    base = f"fb_{(message_id or 'unknown')[:8]}"
    candidate, suffix = base, 1
    while candidate in used:
        suffix += 1
        candidate = f"{base}_{suffix}"
    used.add(candidate)
    return candidate


def build_candidate(
    row: Mapping[str, Any],
    *,
    index: Mapping[SourceKey, str],
    used_ids: set[str],
) -> dict[str, Any]:
    """Turn one feedback row into a golden-dataset-shaped entry."""
    sources = row.get("sources") or []
    if isinstance(sources, str):  # defensive: a store that did not decode JSON
        try:
            sources = json.loads(sources)
        except ValueError:
            sources = []

    chunk_ids, unresolved = resolve_chunk_ids(sources, index)
    rating = row.get("rating") or ""
    rated_at = _parse_rated_at(row)
    message_id = row.get("message_id") or ""
    comment = (row.get("comment") or "").strip()

    query_type = row.get("query_type") or "both"
    if query_type not in VALID_QUERY_TYPES:
        query_type = "both"

    note_parts = [
        f"From user feedback: thumbs {rating or 'unknown'}"
        + (f" on {rated_at.date().isoformat()}" if rated_at else "")
        + f" (message_id={message_id})."
    ]
    if comment:
        note_parts.append(f'Comment: "{comment}".')
    note_parts.append(f"Retrieved sources: {_describe_sources(sources)}.")
    if unresolved:
        note_parts.append(
            f"Could not resolve {len(unresolved)} source(s) to chunk ids; "
            "fill in relevant_chunk_ids by hand."
        )
    answer = (row.get("answer") or "").strip()
    if answer:
        note_parts.append(f"Stored answer: {answer}")

    entry = GoldenEntry(
        id=_unique_id(message_id, used_ids),
        question=(row.get("question") or "").strip(),
        history=[],
        query_type=query_type,
        relevant_chunk_ids=chunk_ids,
        reference_answer="",
        # A downvote is direct evidence the question is hard for the pipeline.
        # An upvote says nothing about difficulty; let the reviewer decide.
        difficulty="hard" if rating == "down" else "medium",
        notes=" ".join(note_parts),
    )
    return entry.to_dict()


def select_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    rating: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[Mapping[str, Any]]:
    """Filter feedback rows by rating and rating time.

    ``since`` is inclusive, ``until`` is exclusive. Rows with no parseable
    ``rated_at`` are kept only when no date filter is active.
    """
    selected = []
    for row in rows:
        if rating and row.get("rating") != rating:
            continue
        if since or until:
            rated_at = _parse_rated_at(row)
            if rated_at is None:
                continue
            if since and rated_at < since:
                continue
            if until and rated_at >= until:
                continue
        selected.append(row)
    return selected


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"expected YYYY-MM-DD, got {value!r}"
        ) from exc


def day_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=timezone.utc)


def day_end_exclusive(day: date) -> datetime:
    """End of ``day`` in UTC as an exclusive bound, so --until includes that day."""
    return day_start(day + timedelta(days=1))


def is_golden_path(path: Path) -> bool:
    return bool(GOLDEN_NAME_RE.match(path.name))


def write_candidates(entries: list[dict[str, Any]], path: Path | str) -> Path:
    """Write the review file. Refuses to touch the golden dataset."""
    out = Path(path).expanduser()
    if is_golden_path(out):
        raise GoldenSetWriteRefused(
            f"refusing to write {out}: candidates go to a separate review file "
            "and are promoted to the golden set by hand"
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(out)
    return out


def export_candidates(
    store: FeedbackStore,
    *,
    rating: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    index: Mapping[SourceKey, str] | None = None,
) -> list[dict[str, Any]]:
    """Read feedback and return golden-dataset-shaped candidates."""
    if index is None:
        index = load_parent_index()
    rows = select_rows(store.get_feedback(), rating=rating, since=since, until=until)
    used_ids: set[str] = set()
    return [build_candidate(row, index=index, used_ids=used_ids) for row in rows]


def _summarize(entries: list[dict[str, Any]], path: Path) -> str:
    if not entries:
        return f"No rated feedback matched; wrote an empty review file to {path}"
    total = len(entries)
    with_chunks = sum(1 for e in entries if e["relevant_chunk_ids"])
    hard = sum(1 for e in entries if e["difficulty"] == "hard")
    return (
        f"Wrote {total} candidate(s) to {path}\n"
        f"  difficulty=hard: {hard}\n"
        f"  with pre-filled relevant_chunk_ids: {with_chunks}/{total}\n"
        "Review them, then copy the ones worth keeping into a golden_dataset*.json by hand."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export rated chat feedback as golden-dataset candidates "
                    "(review file only; never edits the golden set)"
    )
    parser.add_argument(
        "--rating", choices=("up", "down", "both"), default="both",
        help="Only export this rating (default: both)",
    )
    parser.add_argument(
        "--since", type=parse_date, default=None, metavar="YYYY-MM-DD",
        help="Only ratings on or after this UTC date",
    )
    parser.add_argument(
        "--until", type=parse_date, default=None, metavar="YYYY-MM-DD",
        help="Only ratings on or before this UTC date",
    )
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT,
        help=f"Review file to write (default: {DEFAULT_OUTPUT.relative_to(REPO_ROOT)})",
    )
    parser.add_argument(
        "--database-url", default=None,
        help="Feedback database URL (default: $DATABASE_URL, else local feedback.db)",
    )
    parser.add_argument(
        "--parent-lookup", type=Path, default=DEFAULT_PARENT_LOOKUP,
        help="parent_lookup.json used to map sources back to chunk ids",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="Only print the output path",
    )
    args = parser.parse_args(argv)

    if args.since and args.until and args.since > args.until:
        parser.error("--since must not be after --until")

    rating = None if args.rating == "both" else args.rating
    since = day_start(args.since) if args.since else None
    until = day_end_exclusive(args.until) if args.until else None

    index = load_parent_index(args.parent_lookup)
    # FeedbackStore falls back to $DATABASE_URL, then to a local feedback.db.
    store = FeedbackStore(args.database_url)
    entries = export_candidates(
        store, rating=rating, since=since, until=until, index=index
    )
    try:
        out = write_candidates(entries, args.output)
    except GoldenSetWriteRefused as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.quiet:
        print(out)
    else:
        print(_summarize(entries, out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
