"""Pinecone index lifecycle and namespace operations."""

import random
import time
from functools import lru_cache
from typing import Any, Literal

from pinecone import Pinecone, ServerlessSpec

from core.config import Settings, get_settings
from core.exceptions import ConfigurationError, ExternalServiceError


PineconeHealthState = Literal["ready", "missing_key", "index_missing", "unavailable", "invalid_configuration"]


@lru_cache(maxsize=1)
def get_pinecone_client() -> Pinecone:
    """Create the official Pinecone client lazily."""
    settings = get_settings()
    if not settings.pinecone_api_key:
        raise ConfigurationError("PINECONE_API_KEY não configurada")
    return Pinecone(api_key=settings.pinecone_api_key)


def get_index(settings: Settings | None = None) -> Any:
    """Return the configured index handle."""
    settings = settings or get_settings()
    client = get_pinecone_client()
    return client.Index(settings.pinecone_index_name) if hasattr(client, "Index") else client.index(settings.pinecone_index_name)


def setup_index(settings: Settings | None = None) -> dict[str, Any]:
    """Create or validate the configured serverless index idempotently."""
    settings = settings or get_settings()
    client = get_pinecone_client()
    description = _describe_index(client, settings.pinecone_index_name)
    if description is None:
        _create_index(client, settings)
        _wait_for_ready(client, settings.pinecone_index_name)
        description = _describe_index(client, settings.pinecone_index_name)
    _validate_description(description, settings)
    return {"name": settings.pinecone_index_name, "namespace": settings.pinecone_namespace, "dimension": settings.embedding_dimension, "metric": "cosine", "ready": True}


def upsert_vectors(vectors: list[dict[str, Any]], settings: Settings | None = None) -> None:
    """Upsert vectors in batches of at most 100."""
    settings = settings or get_settings()
    index = get_index(settings)
    for start in range(0, len(vectors), 100):
        _with_retry(lambda batch=vectors[start : start + 100]: index.upsert(vectors=batch, namespace=settings.pinecone_namespace))


def query_vectors(vector: list[float], top_k: int, metadata_filter: dict[str, Any] | None, settings: Settings | None = None) -> Any:
    """Query only the configured Pinecone namespace."""
    settings = settings or get_settings()
    return _with_retry(lambda: get_index(settings).query(vector=vector, top_k=top_k, filter=metadata_filter, include_metadata=True, namespace=settings.pinecone_namespace))


def delete_vectors(vector_ids: list[str], settings: Settings | None = None) -> None:
    """Delete vector IDs in batches."""
    if not vector_ids:
        return
    settings = settings or get_settings()
    index = get_index(settings)
    for start in range(0, len(vector_ids), 100):
        _with_retry(lambda batch=vector_ids[start : start + 100]: index.delete(ids=batch, namespace=settings.pinecone_namespace))


def delete_all_vectors(settings: Settings | None = None) -> None:
    """Delete only vectors from the configured namespace."""
    settings = settings or get_settings()
    _with_retry(lambda: get_index(settings).delete(delete_all=True, namespace=settings.pinecone_namespace))


def index_health(settings: Settings | None = None) -> PineconeHealthState:
    """Classify Pinecone readiness without leaking provider details."""
    settings = settings or get_settings()
    if not settings.pinecone_api_key:
        return "missing_key"
    try:
        description = _describe_index(get_pinecone_client(), settings.pinecone_index_name)
        if description is None:
            return "index_missing"
        if not _matches_configuration(description, settings):
            return "invalid_configuration"
        return "ready" if _is_ready(description) else "unavailable"
    except Exception:
        return "unavailable"


def _describe_index(client: Pinecone, name: str) -> Any:
    """Support the SDK 9.x public index-management surface."""
    manager = getattr(client, "indexes", None)
    return manager.describe(name) if manager and _index_exists(manager, name) else None


def _index_exists(manager: Any, name: str) -> bool:
    """Check index existence while preserving provider errors for classification."""
    return any(_index_name(item) == name for item in manager.list())


def _index_name(item: Any) -> str:
    """Read an index name from SDK objects or dictionaries."""
    return item.get("name", "") if isinstance(item, dict) else getattr(item, "name", "")


def _create_index(client: Pinecone, settings: Settings) -> None:
    """Create the required serverless dense index."""
    manager = getattr(client, "indexes", None)
    if manager:
        manager.create(name=settings.pinecone_index_name, vector_type="dense", dimension=settings.embedding_dimension, metric="cosine", spec=ServerlessSpec(cloud=settings.pinecone_cloud, region=settings.pinecone_region), deletion_protection="disabled", tags={"environment": settings.app_env})
        return
    client.create_index(name=settings.pinecone_index_name, vector_type="dense", dimension=settings.embedding_dimension, metric="cosine", spec=ServerlessSpec(cloud=settings.pinecone_cloud, region=settings.pinecone_region), deletion_protection="disabled", tags={"environment": settings.app_env})


def _wait_for_ready(client: Pinecone, name: str) -> None:
    """Poll index readiness with a bounded wait."""
    for _ in range(60):
        description = client.indexes.describe(name)
        if _is_ready(description):
            return
        time.sleep(2)
    raise ExternalServiceError("Pinecone não deixou o índice pronto no tempo esperado")


def _is_ready(description: Any) -> bool:
    """Read readiness from SDK objects or dictionaries."""
    status = getattr(description, "status", None) or (description.get("status", {}) if isinstance(description, dict) else {})
    return bool(getattr(status, "ready", None) if not isinstance(status, dict) else status.get("ready"))


def _matches_configuration(description: Any, settings: Settings) -> bool:
    """Check only the essential index shape configured by the application."""
    dimension = getattr(description, "dimension", None) or (description.get("dimension") if isinstance(description, dict) else None)
    metric = getattr(description, "metric", None) or (description.get("metric") if isinstance(description, dict) else None)
    return dimension == settings.embedding_dimension and metric == "cosine"


def _validate_description(description: Any, settings: Settings) -> None:
    """Reject incompatible indexes without deleting or recreating them."""
    dimension = getattr(description, "dimension", None) or (description.get("dimension") if isinstance(description, dict) else None)
    metric = getattr(description, "metric", None) or (description.get("metric") if isinstance(description, dict) else None)
    if dimension != settings.embedding_dimension or metric != "cosine" or not _is_ready(description):
        raise ConfigurationError("Índice Pinecone incompatível ou indisponível")


def _with_retry(operation):
    """Retry transient Pinecone SDK failures three times."""
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            return operation()
        except Exception as error:
            last_error = error
            if attempt == 2:
                break
            time.sleep((2**attempt) + random.uniform(0, 0.25))
    raise ExternalServiceError("Falha ao chamar o serviço Pinecone") from last_error
