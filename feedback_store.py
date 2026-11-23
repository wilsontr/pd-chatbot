"""Persistent storage for chat responses and user feedback ratings.

Every assistant response is stored with its own id at generation time, so a
rating always has the full question/answer/sources context regardless of the
in-memory response cache. Postgres is used when ``DATABASE_URL`` is set
(production on Railway); otherwise a local SQLite file is used for development
and tests.
"""

from __future__ import annotations

import json
import logging
import os
import uuid

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    Table,
    Text,
    create_engine,
    func,
    insert,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

logger = logging.getLogger(__name__)

_DEFAULT_SQLITE_URL = "sqlite:///feedback.db"

metadata = MetaData()

responses = Table(
    "responses",
    metadata,
    Column("message_id", Text, primary_key=True),
    Column("question", Text, nullable=False),
    Column("answer", Text, nullable=False),
    Column("sources", Text, nullable=False, server_default="[]"),  # JSON list
    Column("query_type", Text, nullable=False, server_default=""),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

ratings = Table(
    "ratings",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("message_id", Text, ForeignKey("responses.message_id"), nullable=False, unique=True),
    Column("rating", Text, nullable=False),
    Column("comment", Text, nullable=False, server_default=""),
    Column("timestamp", BigInteger, nullable=False),  # client-signed unix seconds
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("rating IN ('up', 'down')", name="ratings_rating_check"),
)


def _normalize_url(url: str) -> str:
    """Railway (and Heroku-style) URLs use postgres:// or postgresql://; SQLAlchemy
    needs the driver named explicitly to use psycopg 3."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


class UnknownMessageError(LookupError):
    """Raised when a rating refers to a message id that was never stored."""


class FeedbackStore:
    def __init__(self, url: str | None = None):
        url = url or os.environ.get("DATABASE_URL") or _DEFAULT_SQLITE_URL
        if url.startswith("sqlite") and os.environ.get("RAILWAY_ENVIRONMENT_NAME"):
            logger.warning(
                "DATABASE_URL is not set on Railway; feedback is stored in a local "
                "SQLite file and will be lost on the next deploy"
            )
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        self.engine: Engine = create_engine(
            _normalize_url(url), pool_pre_ping=True, connect_args=connect_args
        )
        if self.engine.dialect.name == "sqlite":
            # SQLite only enforces foreign keys when asked to, per connection.
            from sqlalchemy import event

            @event.listens_for(self.engine, "connect")
            def _fk_on(dbapi_conn, _record):
                dbapi_conn.execute("PRAGMA foreign_keys=ON")

        metadata.create_all(self.engine)

    def save_response(self, question: str, answer: str, sources: list[dict], query_type: str) -> str:
        """Store a completed response and return its new unique message id."""
        message_id = uuid.uuid4().hex
        with self.engine.begin() as conn:
            conn.execute(
                insert(responses).values(
                    message_id=message_id,
                    question=question,
                    answer=answer,
                    sources=json.dumps(sources),
                    query_type=query_type or "",
                )
            )
        return message_id

    def save_rating(self, message_id: str, rating: str, timestamp: int, comment: str = "") -> bool:
        """Store a rating. Returns False if this message was already rated (replay).

        Raises UnknownMessageError if the message id was never stored.
        """
        with self.engine.begin() as conn:
            exists = conn.execute(
                select(responses.c.message_id).where(responses.c.message_id == message_id)
            ).first()
            if exists is None:
                raise UnknownMessageError(message_id)
        try:
            with self.engine.begin() as conn:
                conn.execute(
                    insert(ratings).values(
                        message_id=message_id, rating=rating, comment=comment, timestamp=timestamp
                    )
                )
        except IntegrityError:
            return False
        return True

    def get_feedback(self) -> list[dict]:
        """All ratings joined with their response context, oldest first."""
        stmt = (
            select(
                ratings.c.message_id,
                responses.c.question,
                responses.c.answer,
                responses.c.sources,
                responses.c.query_type,
                ratings.c.rating,
                ratings.c.comment,
                ratings.c.timestamp,
            )
            .join(responses, responses.c.message_id == ratings.c.message_id)
            .order_by(ratings.c.id)
        )
        with self.engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        return [{**row, "sources": json.loads(row["sources"])} for row in rows]
