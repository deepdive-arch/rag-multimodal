"""Semantic retrieval, Postgres validity checks and source diversification."""

import asyncio
from dataclasses import dataclass
from typing import Any

from core.config import Settings, get_settings
from core.exceptions import RetrievalError
from db.catalog import Catalog
from services.embeddings import embed_text, prepare_query_text
from services.pinecone_service import query_vectors
from core.visitor import parse_visitor_id


@dataclass(frozen=True)
class RetrievedSource:
    """Source returned from Pinecone and safe to pass to generation/UI."""

    doc_id: str
    chunk_id: str
    file_name: str
    stored_name: str
    file_type: str
    mime_type: str
    content_modality: str
    page_number: int
    chunk_text: str
    text_preview: str
    media_key: str
    duration_seconds: float
    score: float


def retrieve(
    question: str,
    top_k: int,
    *,
    file_type: str | None = None,
    doc_id: str | None = None,
    visitor_id: str | None = None,
    settings: Settings | None = None,
) -> list[RetrievedSource]:
    """Retrieve diverse, thresholded sources using controlled metadata filters."""
    settings = settings or get_settings()
    if not question.strip():
        raise RetrievalError("A pergunta não pode ser vazia")
    if not 1 <= top_k <= settings.max_top_k:
        raise RetrievalError("top_k fora do limite configurado")
    metadata_filter = _build_filter(file_type, doc_id, visitor_id)
    candidate_k = min(max(top_k * 3, top_k), 50)
    response = query_vectors(embed_text(prepare_query_text(question), settings), candidate_k, metadata_filter, settings, visitor_id)
    matches = _matches(response)
    candidates = [_source_from_match(match) for match in matches if _valid_match(match, settings, visitor_id)]
    candidates = _only_persisted_ready(candidates, settings, visitor_id)
    return _diversify(_unique_sources(candidates), top_k, settings.max_matches_per_document)


def _build_filter(file_type: str | None, doc_id: str | None, visitor_id: str | None = None) -> dict[str, Any] | None:
    """Build only the supported filter expressions."""
    owner = {"visitor_id": {"$eq": parse_visitor_id(visitor_id)}} if visitor_id and parse_visitor_id(visitor_id) else None
    filters = [item for item in (owner, {"file_type": {"$eq": file_type}} if file_type else None, {"doc_id": {"$eq": doc_id}} if doc_id else None) if item]
    if len(filters) == 1:
        return filters[0]
    if filters:
        return {"$and": filters}
    return None


def _matches(response: Any) -> list[Any]:
    """Normalize SDK responses to a list."""
    return list(
        getattr(response, "matches", None) or (response.get("matches", []) if isinstance(response, dict) else [])
    )


def _valid_match(match: Any, settings: Settings, visitor_id: str | None = None) -> bool:
    """Check score and required metadata fields."""
    score = float(getattr(match, "score", None) or (match.get("score", 0) if isinstance(match, dict) else 0))
    metadata = getattr(match, "metadata", None) or (match.get("metadata", {}) if isinstance(match, dict) else {})
    owner_ok = not visitor_id or metadata.get("visitor_id") == parse_visitor_id(visitor_id)
    return score >= settings.min_relevance_score and owner_ok and bool(metadata.get("doc_id")) and bool(metadata.get("chunk_id"))


def _source_from_match(match: Any) -> RetrievedSource:
    """Map a Pinecone match into a typed source."""
    metadata = getattr(match, "metadata", None) or (match.get("metadata", {}) if isinstance(match, dict) else {})
    score = float(getattr(match, "score", None) or (match.get("score", 0) if isinstance(match, dict) else 0))
    return RetrievedSource(
        metadata.get("doc_id", ""),
        metadata.get("chunk_id", ""),
        metadata.get("file_name", metadata.get("original_name", "Arquivo")),
        metadata.get("stored_name", ""),
        metadata.get("file_type", "text"),
        metadata.get("mime_type", "text/plain"),
        metadata.get("content_modality", "text"),
        int(metadata.get("page", metadata.get("page_number", 0))),
        metadata.get("chunk_text", ""),
        metadata.get("text_preview", ""),
        metadata.get("object_key", metadata.get("media_key", "")),
        float(metadata.get("duration_seconds", 0.0)),
        score,
    )


def _only_persisted_ready(sources: list[RetrievedSource], settings: Settings, visitor_id: str | None = None) -> list[RetrievedSource]:
    """Reject stale Pinecone matches when the Postgres catalog is available."""
    if not settings.database_url or not sources:
        return sources
    refs = [
        {"chunk_id": source.chunk_id, "doc_id": source.doc_id, "object_key": source.media_key} for source in sources
    ]
    catalog = Catalog.ephemeral(settings)
    try:
        valid = asyncio.run(catalog.valid_chunk_refs(refs, visitor_id))
    except Exception as error:
        raise RetrievalError("Não foi possível validar as fontes persistidas.") from error
    finally:
        asyncio.run(catalog.close())
    return [source for source in sources if source.chunk_id in valid]


def _unique_sources(sources: list[RetrievedSource]) -> list[RetrievedSource]:
    """Keep one source per chunk when the vector service returns duplicates."""
    return list({source.chunk_id: source for source in sources}.values())


def _diversify(sources: list[RetrievedSource], top_k: int, per_document: int) -> list[RetrievedSource]:
    """Prefer distinct documents before filling remaining slots."""
    selected: list[RetrievedSource] = []
    counts: dict[str, int] = {}
    selected_chunks: set[str] = set()
    for source in sources:
        if source.chunk_id not in selected_chunks and counts.get(source.doc_id, 0) == 0:
            selected.append(source)
            selected_chunks.add(source.chunk_id)
            counts[source.doc_id] = 1
    for source in sources:
        if len(selected) >= top_k:
            break
        if source.chunk_id not in selected_chunks and counts.get(source.doc_id, 0) < per_document:
            selected.append(source)
            selected_chunks.add(source.chunk_id)
            counts[source.doc_id] = counts.get(source.doc_id, 0) + 1
    return sorted(selected[:top_k], key=lambda item: item.score, reverse=True)
