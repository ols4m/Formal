"""Canonical items and adapter/repository contracts for Formal intake."""

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Protocol, Sequence

from core.db import get_db, init_db


ItemType = Literal["task", "event", "note", "transaction", "file"]
ITEM_TYPES = {"task", "event", "note", "transaction", "file"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class Item:
    """Normalized item shape shared by source adapters and the rest of Formal."""

    source: str
    item_type: ItemType
    title: str
    source_item_id: str | None = None
    description: str | None = None
    due_at: str | None = None
    importance: int = 3
    effort_minutes: float | None = None
    consequence: int = 3
    status: str = "open"
    raw_payload: dict = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = field(default_factory=_utc_now)
    updated_at: str = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise ValueError("Item source cannot be empty.")
        if not self.title.strip():
            raise ValueError("Item title cannot be empty.")
        if self.item_type not in ITEM_TYPES:
            raise ValueError(f"Unsupported item type: {self.item_type!r}.")
        if not 1 <= self.importance <= 5:
            raise ValueError("Item importance must be between 1 and 5.")
        if not 1 <= self.consequence <= 5:
            raise ValueError("Item consequence must be between 1 and 5.")
        if self.effort_minutes is not None and self.effort_minutes < 0:
            raise ValueError("Item effort_minutes cannot be negative.")
        if self.due_at is not None:
            try:
                datetime.fromisoformat(self.due_at.replace("Z", "+00:00"))
            except ValueError as error:
                raise ValueError("Item due_at must be an ISO-8601 date or datetime.") from error
        if not isinstance(self.raw_payload, dict):
            raise ValueError("Item raw_payload must be a JSON object.")


class ItemAdapter(Protocol):
    """Contract implemented by each external or manual intake source."""

    source: str

    async def fetch(self) -> Sequence[Item]:
        """Fetch and normalize all currently available source records."""
        ...


class ItemRepository:
    """Persist normalized items and retrieve them independently of their source."""

    def __init__(self, connection: sqlite3.Connection | None = None):
        self._owns_connection = connection is None
        if connection is None:
            init_db()
            connection = get_db()
        self._connection = connection

    def close(self) -> None:
        if self._owns_connection:
            self._connection.close()

    def upsert(self, items: Sequence[Item]) -> int:
        """Insert or update fetched items; return the number of records processed."""
        now = _utc_now()
        with self._connection:
            for item in items:
                self._connection.execute(
                    """
                    INSERT INTO items (
                        id, source, source_item_id, item_type, title, description,
                        due_at, importance, effort_minutes, consequence, status,
                        raw_payload, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source, source_item_id) WHERE source_item_id IS NOT NULL
                    DO UPDATE SET
                        item_type = excluded.item_type,
                        title = excluded.title,
                        description = excluded.description,
                        due_at = excluded.due_at,
                        importance = excluded.importance,
                        effort_minutes = excluded.effort_minutes,
                        consequence = excluded.consequence,
                        raw_payload = excluded.raw_payload,
                        updated_at = excluded.updated_at
                    """,
                    (
                        item.id,
                        item.source,
                        item.source_item_id,
                        item.item_type,
                        item.title,
                        item.description,
                        item.due_at,
                        item.importance,
                        item.effort_minutes,
                        item.consequence,
                        item.status,
                        json.dumps(item.raw_payload, ensure_ascii=False),
                        item.created_at,
                        now,
                    ),
                )
        return len(items)

    def list_items(
        self,
        *,
        status: str | None = "open",
        limit: int = 100,
    ) -> list[dict]:
        if limit <= 0:
            raise ValueError("Item query limit must be positive.")
        if status is None:
            rows = self._connection.execute(
                "SELECT * FROM items ORDER BY due_at IS NULL, due_at, created_at LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = self._connection.execute(
                """SELECT * FROM items WHERE status = ?
                   ORDER BY due_at IS NULL, due_at, created_at LIMIT ?""",
                (status, limit),
            ).fetchall()
        return [
            {**dict(row), "raw_payload": json.loads(row["raw_payload"])}
            for row in rows
        ]


async def sync_adapter(adapter: ItemAdapter, repository: ItemRepository) -> int:
    """Fetch normalized records from one adapter and persist them."""
    items = await adapter.fetch()
    if not all(item.source == adapter.source for item in items):
        raise ValueError(
            f"Adapter {adapter.source!r} returned an item with a mismatched source."
        )
    return repository.upsert(items)
