"""SQLite schema and pragmas."""

import aiosqlite


SCHEMA = """
CREATE TABLE IF NOT EXISTS ingested_files (
    doc_id TEXT PRIMARY KEY,
    original_name TEXT NOT NULL,
    stored_name TEXT NOT NULL,
    storage_key TEXT NOT NULL,
    file_type TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('processing', 'ready', 'failed', 'deleting')),
    chunks_count INTEGER NOT NULL DEFAULT 0,
    warnings_json TEXT NOT NULL DEFAULT '[]',
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL,
    vector_id TEXT NOT NULL UNIQUE,
    chunk_index INTEGER NOT NULL,
    page_number INTEGER NOT NULL DEFAULT 0,
    content_modality TEXT NOT NULL,
    media_key TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY (doc_id) REFERENCES ingested_files(doc_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS feedback (
    id TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    useful INTEGER NOT NULL CHECK (useful IN (0, 1)),
    source_ids_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ingested_files_status ON ingested_files(status);
CREATE INDEX IF NOT EXISTS idx_chunks_doc_id ON chunks(doc_id);
CREATE INDEX IF NOT EXISTS idx_chunks_modality ON chunks(content_modality);
"""


async def initialize_database(database_path: str) -> None:
    """Create the schema and enable safe SQLite pragmas."""
    async with aiosqlite.connect(database_path) as connection:
        await connection.execute("PRAGMA foreign_keys = ON")
        await connection.execute("PRAGMA journal_mode = WAL")
        await connection.executescript(SCHEMA)
        await connection.commit()
