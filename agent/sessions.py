"""Patches stored per conversation session, for iterative edits (TASK-5.12).

A request's :class:`~agent.workspace.PatchWorkspace` only lives as long as the
request. Follow-ups such as "make the filter resonant" need the patch from the
previous turn, so the workspace is seeded from (and written back to) the same
Postgres/SQLite database that holds the run log.

Each stored patch has a stable short ``patch_id`` — what ``list_patches`` and the
``/sessions/{session_id}/patches`` endpoint list — plus the ``name`` the agent
uses to address it. Writes replace the row for ``(session_id, name)``, so the id
survives revisions and downloading ``/sessions/{id}/patches/{patch_id}.pd`` always
returns the latest revision.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from sqlalchemy import (
    Column,
    DateTime,
    Engine,
    Integer,
    Table,
    Text,
    UniqueConstraint,
    delete,
    func,
    insert,
    or_,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError

from pd_schema import PdPatch

from .store import _iso, _utcnow, make_engine, metadata
from .workspace import PatchEntry, PatchWorkspace

logger = logging.getLogger(__name__)

#: Rows older than this are removed opportunistically when a patch is written.
RETENTION_DAYS = 30
_PRUNE_EVERY = timedelta(hours=1)

session_patches = Table(
    "session_patches",
    metadata,
    Column("patch_id", Text, primary_key=True),
    Column("session_id", Text, nullable=False, index=True),
    Column("name", Text, nullable=False),
    Column("patch", Text, nullable=False),  # JSON object
    Column("revision", Integer, nullable=False, server_default="1"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("session_id", "name", name="uq_session_patches_session_name"),
)


def new_patch_id() -> str:
    """A short id the model can copy: ``p`` plus 11 hex characters."""
    return "p" + uuid.uuid4().hex[:11]


def patch_pd_url(session_id: str, patch_id: str) -> str:
    """The API path that downloads a patch's latest revision as a ``.pd`` file."""
    return f"/sessions/{quote(session_id, safe='')}/patches/{quote(patch_id, safe='')}.pd"


@dataclass(frozen=True)
class StoredSessionPatch:
    patch_id: str
    session_id: str
    name: str
    patch: PdPatch
    revision: int
    created_at: str | None = None
    updated_at: str | None = None

    @property
    def pd_url(self) -> str:
        return patch_pd_url(self.session_id, self.patch_id)

    def to_dict(self) -> dict[str, Any]:
        """The listing shape: small enough to send for every session patch."""
        return {
            "id": self.patch_id,
            "name": self.name,
            "revision": self.revision,
            "boxes": len(self.patch.objects),
            "updated_at": self.updated_at,
            "pd_url": self.pd_url,
        }

    def to_entry(self) -> PatchEntry:
        return PatchEntry(
            name=self.name,
            patch=self.patch,
            revisions=self.revision,
            patch_id=self.patch_id,
            pd_url=self.pd_url,
        )


class SessionPatchStore:
    """The latest revision of each named patch per session, in the app database."""

    def __init__(self, url: str | None = None, *, engine: Engine | None = None):
        self.engine: Engine = engine or make_engine(url)
        metadata.create_all(self.engine)
        self._last_prune: datetime | None = None

    # --- reads ---------------------------------------------------------------

    def list_for_session(self, session_id: str) -> list[StoredSessionPatch]:
        """Every patch in a session, oldest first (the order they were written)."""
        stmt = (
            select(session_patches)
            .where(session_patches.c.session_id == session_id)
            .order_by(session_patches.c.created_at.asc(), session_patches.c.name.asc())
        )
        with self.engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        return [self._from_row(row) for row in rows]

    def get(self, session_id: str, patch_id_or_name: str) -> StoredSessionPatch | None:
        """One stored patch, looked up by its id or by its name."""
        stmt = select(session_patches).where(
            session_patches.c.session_id == session_id,
            or_(
                session_patches.c.patch_id == patch_id_or_name,
                session_patches.c.name == patch_id_or_name,
            ),
        )
        with self.engine.connect() as conn:
            row = conn.execute(stmt).mappings().first()
        return self._from_row(row) if row is not None else None

    def load_workspace(self, session_id: str) -> PatchWorkspace:
        """A workspace holding the session's patches, ready for the agent to edit."""
        workspace = PatchWorkspace()
        self.seed_workspace(session_id, workspace)
        return workspace

    # --- writes --------------------------------------------------------------

    def save(
        self, session_id: str, name: str, patch: PdPatch, *, revision: int = 1
    ) -> StoredSessionPatch:
        """Upsert the patch named ``name`` in ``session_id`` and return the row.

        The ``patch_id`` is kept across revisions; the revision never goes
        backwards, even if an older answer is replayed from the cache.
        """
        self._prune_if_due()
        existing = self._row_by_name(session_id, name)
        now = _utcnow()
        if existing is None:
            patch_id = new_patch_id()
            try:
                with self.engine.begin() as conn:
                    conn.execute(
                        insert(session_patches).values(
                            patch_id=patch_id,
                            session_id=session_id,
                            name=name,
                            patch=json.dumps(patch.model_dump()),
                            revision=max(1, int(revision)),
                            created_at=now,
                            updated_at=now,
                        )
                    )
            except IntegrityError:
                # A concurrent request created the same (session, name) first.
                existing = self._row_by_name(session_id, name)
                if existing is None:
                    raise
                return self._update(existing, patch, revision, now)
            return StoredSessionPatch(
                patch_id=patch_id,
                session_id=session_id,
                name=name,
                patch=patch,
                revision=max(1, int(revision)),
                created_at=_iso(now),
                updated_at=_iso(now),
            )
        return self._update(existing, patch, revision, now)

    def save_workspace(
        self, session_id: str, workspace: PatchWorkspace
    ) -> list[StoredSessionPatch]:
        """Persist every patch the workspace wrote or repaired this run."""
        saved: list[StoredSessionPatch] = []
        for entry in workspace.entries.values():
            if entry.patch_id is not None and not entry.touched:
                continue
            stored = self.save(
                session_id, entry.name, entry.patch, revision=entry.revisions
            )
            entry.patch_id = stored.patch_id
            entry.pd_url = stored.pd_url
            saved.append(stored)
        return saved

    def seed_workspace(self, session_id: str, workspace: PatchWorkspace) -> PatchWorkspace:
        """Add the session's stored patches to ``workspace``.

        Entries the caller already put in the workspace are left alone, so an
        explicit workspace (tests, evaluation) keeps its own patches.
        """
        for stored in self.list_for_session(session_id):
            if stored.name not in workspace.entries:
                workspace.entries[stored.name] = stored.to_entry()
        return workspace

    def delete(self, session_id: str, patch_id_or_name: str) -> bool:
        """Remove one patch; returns whether a row was deleted."""
        stmt = delete(session_patches).where(
            session_patches.c.session_id == session_id,
            or_(
                session_patches.c.patch_id == patch_id_or_name,
                session_patches.c.name == patch_id_or_name,
            ),
        )
        with self.engine.begin() as conn:
            return bool(conn.execute(stmt).rowcount)

    def prune(self, older_than_days: int = RETENTION_DAYS) -> int:
        """Delete patches not updated within the retention window; returns the count."""
        cutoff = _utcnow() - timedelta(days=older_than_days)
        with self.engine.begin() as conn:
            result = conn.execute(
                delete(session_patches).where(session_patches.c.updated_at < cutoff)
            )
        self._last_prune = _utcnow()
        return int(result.rowcount or 0)

    # --- internals -----------------------------------------------------------

    def _prune_if_due(self) -> None:
        now = datetime.now(timezone.utc)
        if self._last_prune is not None and now - self._last_prune < _PRUNE_EVERY:
            return
        try:
            self.prune()
        except Exception:  # a cleanup failure must not fail a patch write
            logger.warning("session patch prune failed", exc_info=True)

    def _update(
        self,
        existing: StoredSessionPatch,
        patch: PdPatch,
        revision: int,
        now: datetime,
    ) -> StoredSessionPatch:
        revision = max(existing.revision, int(revision))
        with self.engine.begin() as conn:
            conn.execute(
                update(session_patches)
                .where(session_patches.c.patch_id == existing.patch_id)
                .values(
                    patch=json.dumps(patch.model_dump()),
                    revision=revision,
                    updated_at=now,
                )
            )
        return StoredSessionPatch(
            patch_id=existing.patch_id,
            session_id=existing.session_id,
            name=existing.name,
            patch=patch,
            revision=revision,
            created_at=existing.created_at,
            updated_at=_iso(now),
        )

    def _row_by_name(self, session_id: str, name: str) -> StoredSessionPatch | None:
        stmt = select(session_patches).where(
            session_patches.c.session_id == session_id,
            session_patches.c.name == name,
        )
        with self.engine.connect() as conn:
            row = conn.execute(stmt).mappings().first()
        return self._from_row(row) if row is not None else None

    @staticmethod
    def _from_row(row: Any) -> StoredSessionPatch:
        return StoredSessionPatch(
            patch_id=row["patch_id"],
            session_id=row["session_id"],
            name=row["name"],
            patch=PdPatch.model_validate(json.loads(row["patch"])),
            revision=int(row["revision"]),
            created_at=_iso(row["created_at"]),
            updated_at=_iso(row["updated_at"]),
        )
