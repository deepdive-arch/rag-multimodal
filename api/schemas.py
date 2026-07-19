"""Pydantic request and response contracts."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator


FileType = Literal["pdf", "image", "video", "audio", "text", "docx"]
AnswerMode = Literal["quick", "detailed", "evidence"]
HealthState = Literal["ok", "degraded", "offline"]
DatabaseHealthState = Literal["ok", "unavailable"]
GoogleHealthState = Literal["configured", "missing_key"]
R2HealthState = Literal["ready", "missing_config", "unavailable"]
PineconeHealthState = Literal["ready", "missing_key", "index_missing", "unavailable", "invalid_configuration"]
UploadStatus = Literal["pending_upload", "uploaded", "processing", "indexing", "ready", "failed", "deleting", "deleted"]


class HealthServices(BaseModel):
    """Readiness state for each service used by the application."""

    database: DatabaseHealthState
    r2: R2HealthState
    google: GoogleHealthState
    gemini: GoogleHealthState
    pinecone: PineconeHealthState


class HealthModels(BaseModel):
    """Models configured for embeddings and generation."""

    embedding: str
    generation: str


class PublicDemoConfig(BaseModel):
    """Safe public limits that the frontend may display."""

    enabled: bool
    formats: list[str]
    max_upload_size_mb: int
    max_daily_uploads: int
    max_daily_queries: int
    retention_days: int
    max_pdf_pages: int
    max_audio_duration_seconds: int
    max_video_duration_seconds: int


class HealthStatus(BaseModel):
    """Safe public health contract."""

    status: HealthState
    services: HealthServices
    models: HealthModels
    public_demo: PublicDemoConfig


class ChatHistoryMessage(BaseModel):
    """One bounded chat history message."""

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)


class QueryFilters(BaseModel):
    """Supported safe query filters."""

    model_config = ConfigDict(extra="forbid")

    file_type: FileType | None = None
    doc_id: str | None = Field(default=None, min_length=1, max_length=128)


class QueryRequest(BaseModel):
    """RAG query request."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=8000)
    top_k: int = Field(default=5, ge=1, le=20)
    history: list[ChatHistoryMessage] = Field(default_factory=list, max_length=50)
    filters: QueryFilters = Field(default_factory=QueryFilters)
    answer_mode: AnswerMode = "detailed"
    conversation_id: UUID | None = None


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
    response_id: str | None = None
    conversation_id: str | None = None
    message_id: str | None = None


class ConversationMessageResponse(BaseModel):
    """One visitor-owned persisted query response."""

    message_id: str
    response_id: str | None = None
    conversation_id: str
    question: str
    answer: str
    source_ids: list[str]
    sources: list[SourceResponse]
    insufficient_context: bool
    created_at: str | None


class ConversationResponse(BaseModel):
    """Visitor-scoped conversation history."""

    conversation_id: str
    messages: list[ConversationMessageResponse]


class IngestResponse(BaseModel):
    """Ingestion result."""

    doc_id: str
    name: str
    file_type: str
    chunks: int
    duplicate: bool
    warnings: list[str]


class UploadPresignRequest(BaseModel):
    """Browser authorization request for one small public upload."""

    model_config = ConfigDict(extra="forbid")

    file_name: str = Field(min_length=1, max_length=512)
    size_bytes: int = Field(ge=1)
    mime_type: str = Field(min_length=1, max_length=255)
    sha256: str = Field(min_length=64, max_length=64)

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        """Require a lowercase hexadecimal SHA-256 digest."""
        if any(character not in "0123456789abcdefABCDEF" for character in value):
            raise ValueError("sha256 deve ser hexadecimal")
        return value.lower()


class UploadPresignResponse(BaseModel):
    """Safe presigned upload contract without permanent credentials."""

    doc_id: str
    upload_url: str | None
    headers: dict[str, str]
    expires_in: int
    duplicate: bool
    status: UploadStatus


class UploadCompleteResponse(BaseModel):
    """State returned after the backend validates an uploaded R2 object."""

    doc_id: str
    duplicate: bool
    status: UploadStatus
    chunks: int = 0
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None


class ClearIndexRequest(BaseModel):
    """Confirmation body for namespace clearing."""

    confirmation: Literal["DELETE_ALL"]


class FeedbackRequest(BaseModel):
    """Feedback for one response previously persisted by the RAG pipeline."""

    model_config = ConfigDict(extra="forbid")

    response_id: UUID
    useful: StrictBool
