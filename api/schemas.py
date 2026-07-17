"""Pydantic request and response contracts."""

from typing import Literal

from pydantic import BaseModel, Field


FileType = Literal["pdf", "image", "video", "audio", "text", "docx"]
AnswerMode = Literal["quick", "detailed", "evidence"]


class ChatHistoryMessage(BaseModel):
    """One bounded chat history message."""

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)


class QueryFilters(BaseModel):
    """Supported safe query filters."""

    file_type: FileType | None = None
    doc_id: str | None = Field(default=None, min_length=1, max_length=128)


class QueryRequest(BaseModel):
    """RAG query request."""

    question: str = Field(min_length=1, max_length=8000)
    top_k: int = Field(default=5, ge=1, le=20)
    history: list[ChatHistoryMessage] = Field(default_factory=list, max_length=50)
    filters: QueryFilters = Field(default_factory=QueryFilters)
    answer_mode: AnswerMode = "detailed"


class SourceResponse(BaseModel):
    """Safe source response for the frontend."""

    doc_id: str
    chunk_id: str
    file_name: str
    file_type: str
    content_modality: str
    page_number: int
    text_preview: str
    media_url: str | None
    score: float


class QueryResponse(BaseModel):
    """RAG answer response."""

    answer: str
    sources: list[SourceResponse]
    insufficient_context: bool


class IngestResponse(BaseModel):
    """Ingestion result."""

    doc_id: str
    name: str
    file_type: str
    chunks: int
    duplicate: bool
    warnings: list[str]


class ClearIndexRequest(BaseModel):
    """Confirmation body for namespace clearing."""

    confirmation: Literal["DELETE_ALL"]


class FeedbackRequest(BaseModel):
    """Answer feedback payload."""

    question: str = Field(min_length=1, max_length=8000)
    answer: str = Field(min_length=1, max_length=20000)
    useful: bool
    source_ids: list[str] = Field(default_factory=list, max_length=20)
