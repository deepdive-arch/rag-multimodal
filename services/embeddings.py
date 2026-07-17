"""Gemini embedding adapter with bounded retries."""

import random
import time
from functools import lru_cache

from google import genai
from google.genai import types

from core.config import Settings, get_settings
from core.exceptions import ConfigurationError, ExternalServiceError


@lru_cache(maxsize=1)
def get_google_client() -> genai.Client:
    """Create the official Gemini client lazily."""
    settings = get_settings()
    if not settings.google_api_key:
        raise ConfigurationError("GOOGLE_API_KEY não configurada")
    return genai.Client(api_key=settings.google_api_key)


def prepare_document_text(text: str, title: str | None) -> str:
    """Prepare a document chunk for embedding."""
    safe_title = title.strip() if title and title.strip() else "none"
    return f"title: {safe_title} | text: {text}"


def prepare_query_text(question: str) -> str:
    """Prepare a user question for embedding."""
    return f"task: question answering | query: {question}"


def embed_text(text: str, settings: Settings | None = None) -> list[float]:
    """Embed text using the configured Gemini embedding model."""
    settings = settings or get_settings()
    response = _with_retry(lambda: get_google_client().models.embed_content(model=settings.gemini_embedding_model, contents=text, config=types.EmbedContentConfig(output_dimensionality=settings.embedding_dimension)))
    values = list(response.embeddings[0].values)
    return _validate_dimension(values, settings)


def embed_media(data: bytes, mime_type: str, settings: Settings | None = None) -> list[float]:
    """Embed one media payload using Gemini multimodal embeddings."""
    settings = settings or get_settings()
    part = types.Part.from_bytes(data=data, mime_type=mime_type)
    response = _with_retry(lambda: get_google_client().models.embed_content(model=settings.gemini_embedding_model, contents=part, config=types.EmbedContentConfig(output_dimensionality=settings.embedding_dimension)))
    values = list(response.embeddings[0].values)
    return _validate_dimension(values, settings)


def _validate_dimension(values: list[float], settings: Settings) -> list[float]:
    """Ensure Pinecone receives exactly the configured dimension."""
    if len(values) != settings.embedding_dimension:
        raise ExternalServiceError("Gemini retornou uma dimensão de embedding incompatível")
    return values


def _with_retry(operation):
    """Retry transient SDK failures three times with exponential backoff."""
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            return operation()
        except Exception as error:
            last_error = error
            if attempt == 2:
                break
            time.sleep((2**attempt) + random.uniform(0, 0.25))
    raise ExternalServiceError("Falha ao chamar o serviço Gemini") from last_error
