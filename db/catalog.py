"""Async transactional catalog operations."""

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from db.migrations import initialize_database


def utc_now() -> str:
    """Return an ISO timestamp in UTC."""
    return datetime.now(UTC).isoformat()


class Catalog:
    """Persistence gateway for local file, chunk and feedback records."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = str(database_path)

    async def initialize(self) -> None:
        """Ensure the catalog exists."""
        await initialize_database(self.database_path)

    async def get_file(self, doc_id: str) -> dict[str, Any] | None:
        """Load one file by document id."""
        return await self._fetch_one("SELECT * FROM ingested_files WHERE doc_id = ?", (doc_id,))

    async def create_processing_file(self, record: dict[str, Any]) -> None:
        """Insert or reset a file record in processing state."""
        now = utc_now()
        values = (record["doc_id"], record["original_name"], record["stored_name"], record["storage_key"], record["file_type"], record["mime_type"], record["size_bytes"], "processing", json.dumps(record.get("warnings", [])), now, now)
        query = """INSERT INTO ingested_files (doc_id, original_name, stored_name, storage_key, file_type, mime_type, size_bytes, status, warnings_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(doc_id) DO UPDATE SET original_name=excluded.original_name, stored_name=excluded.stored_name, storage_key=excluded.storage_key, file_type=excluded.file_type, mime_type=excluded.mime_type, size_bytes=excluded.size_bytes, status='processing', chunks_count=0, warnings_json=excluded.warnings_json, error_message=NULL, updated_at=excluded.updated_at"""
        await self._execute(query, values)

    async def update_file_status(self, doc_id: str, status: str, *, chunks_count: int = 0, warnings: list[str] | None = None, error_message: str | None = None) -> None:
        """Update status and operational result atomically."""
        query = "UPDATE ingested_files SET status = ?, chunks_count = ?, warnings_json = ?, error_message = ?, updated_at = ? WHERE doc_id = ?"
        await self._execute(query, (status, chunks_count, json.dumps(warnings or []), error_message, utc_now(), doc_id))

    async def add_chunks(self, chunks: list[dict[str, Any]]) -> None:
        """Insert chunk rows in a single transaction."""
        if not chunks:
            return
        query = "INSERT INTO chunks (chunk_id, doc_id, vector_id, chunk_index, page_number, content_modality, media_key, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
        values = [(item["chunk_id"], item["doc_id"], item["vector_id"], item["chunk_index"], item.get("page_number", 0), item["content_modality"], item.get("media_key", ""), utc_now()) for item in chunks]
        await self._executemany(query, values)

    async def get_vector_ids(self, doc_id: str) -> list[str]:
        """Return all vector IDs belonging to a document."""
        rows = await self._fetch_all("SELECT vector_id FROM chunks WHERE doc_id = ? ORDER BY chunk_index", (doc_id,))
        return [row["vector_id"] for row in rows]

    async def delete_chunks(self, doc_id: str) -> None:
        """Delete chunks for one document."""
        await self._execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))

    async def delete_file(self, doc_id: str) -> None:
        """Delete one catalog file and its cascaded chunks."""
        await self._execute("DELETE FROM ingested_files WHERE doc_id = ?", (doc_id,))

    async def list_files(self) -> list[dict[str, Any]]:
        """List files without exposing internal paths."""
        return await self._fetch_all("SELECT doc_id, original_name, file_type, mime_type, chunks_count, size_bytes, status, warnings_json, created_at FROM ingested_files ORDER BY created_at DESC", ())

    async def stats(self) -> dict[str, Any]:
        """Return catalog counts grouped by file type."""
        files = await self._fetch_one("SELECT COUNT(*) AS count FROM ingested_files WHERE status = 'ready'", ())
        chunks = await self._fetch_one("SELECT COUNT(*) AS count FROM chunks", ())
        rows = await self._fetch_all("SELECT file_type, COUNT(*) AS count FROM ingested_files WHERE status = 'ready' GROUP BY file_type", ())
        return {"files": files["count"], "chunks": chunks["count"], "by_type": {row["file_type"]: row["count"] for row in rows}}

    async def record_feedback(self, question: str, answer: str, useful: bool, source_ids: list[str]) -> str:
        """Persist feedback and return its generated identifier."""
        feedback_id = str(uuid.uuid4())
        query = "INSERT INTO feedback (id, question, answer, useful, source_ids_json, created_at) VALUES (?, ?, ?, ?, ?, ?)"
        await self._execute(query, (feedback_id, question, answer, int(useful), json.dumps(source_ids), utc_now()))
        return feedback_id

    async def clear_catalog(self) -> None:
        """Clear local records while retaining the schema."""
        async with aiosqlite.connect(self.database_path) as connection:
            await connection.execute("PRAGMA foreign_keys = ON")
            await connection.execute("DELETE FROM feedback")
            await connection.execute("DELETE FROM ingested_files")
            await connection.commit()

    async def is_ready(self) -> bool:
        """Check that the database can answer a trivial query."""
        return (await self._fetch_one("SELECT 1 AS ready", ())) is not None

    async def _fetch_one(self, query: str, parameters: tuple[Any, ...]) -> dict[str, Any] | None:
        """Execute a parameterized query and return one row."""
        async with aiosqlite.connect(self.database_path) as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(query, parameters)
            row = await cursor.fetchone()
            await cursor.close()
        return dict(row) if row else None

    async def _fetch_all(self, query: str, parameters: tuple[Any, ...]) -> list[dict[str, Any]]:
        """Execute a parameterized query and return all rows."""
        async with aiosqlite.connect(self.database_path) as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(query, parameters)
            rows = await cursor.fetchall()
            await cursor.close()
        return [dict(row) for row in rows]

    async def _execute(self, query: str, parameters: tuple[Any, ...]) -> None:
        """Execute one write in a transaction."""
        async with aiosqlite.connect(self.database_path) as connection:
            await connection.execute("PRAGMA foreign_keys = ON")
            await connection.execute(query, parameters)
            await connection.commit()

    async def _executemany(self, query: str, values: list[tuple[Any, ...]]) -> None:
        """Execute many writes in one transaction."""
        async with aiosqlite.connect(self.database_path) as connection:
            await connection.execute("PRAGMA foreign_keys = ON")
            await connection.executemany(query, values)
            await connection.commit()
