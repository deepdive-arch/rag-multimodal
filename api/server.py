"""FastAPI HTTP surface for the multimodal RAG MVP."""

import asyncio
import hmac
import json
import logging
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, File, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.dependencies import require_admin, visitor_id_for_request
from api.schemas import (
    ClearIndexRequest,
    ConversationMessageResponse,
    ConversationResponse,
    DatabaseHealthState,
    FeedbackRequest,
    GoogleHealthState,
    HealthState,
    HealthStatus,
    IngestResponse,
    PineconeHealthState,
    PublicDemoConfig,
    QueryRequest,
    QueryResponse,
    R2HealthState,
    SourceResponse,
    UploadCompleteResponse,
    UploadPresignRequest,
    UploadPresignResponse,
)
from core.config import Settings, get_settings
from core.exceptions import (
    AppError,
    ConfigurationError,
    ExternalServiceError,
    FileNotFoundInCatalogError,
    FileTooLargeError,
    IngestionError,
    InvalidMediaError,
    MediaDurationExceededError,
    ObjectStorageConfigurationError,
    UnsupportedFileError,
    UploadConflictError,
    UploadNotCompleteError,
    CapacityExceededError,
    QuotaExceededError,
)
from core.logging import configure_logging
from core.visitor import VISITOR_COOKIE_NAME, new_visitor_id, sign_visitor_cookie, verify_visitor_cookie
from db.catalog import Catalog
from db.runtime import close_database, configure_database
from services.generation import ChatHistoryMessage as GenerationHistory, generate_answer
from services.deletion import cleanup_pending_documents, delete_document_async
from services.ingestion import clear_index, ingest_file, process_uploaded_document, retry_document
from services.pinecone_service import index_health
from services.retrieval import RetrievedSource, retrieve
from services.storage import (
    file_type_for,
    get_object_storage,
    is_managed_object_key,
    mime_for,
    original_object_key,
    original_object_metadata,
    sanitize_filename,
    save_upload_stream,
    SUPPORTED_EXTENSIONS,
)
from services.abuse import client_hash_for_request, utc_usage_date


logger = logging.getLogger("rag_multimodal")
_cleanup_task: asyncio.Task | None = None


def _schedule_cleanup(settings: Settings) -> None:
    """Start one bounded cleanup task without delaying a request or startup."""
    global _cleanup_task
    if settings.app_env == "test" or not settings.database_url:
        return
    if _cleanup_task is None or _cleanup_task.done():
        _cleanup_task = asyncio.create_task(_run_cleanup(settings))


async def _run_cleanup(settings: Settings) -> None:
    """Run the small cleanup batch under a hard time budget."""
    try:
        await asyncio.wait_for(cleanup_pending_documents(settings), timeout=settings.cleanup_timeout_seconds)
    except Exception:
        logger.warning("bounded_cleanup_failed")


async def _stop_cleanup() -> None:
    """Cancel a background cleanup before the database engine closes."""
    global _cleanup_task
    task, _cleanup_task = _cleanup_task, None
    if task and not task.done():
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Open and verify Postgres before serving requests, then dispose it."""
    configure_logging()
    settings = get_settings()
    database = configure_database(settings)
    database_ready = False
    try:
        if settings.app_env == "test":
            yield
            return
        try:
            await database.check(settings.database_health_timeout_seconds)
            database_ready = True
        except Exception:
            logger.warning("database_startup_check_failed")
        if database_ready:
            _schedule_cleanup(settings)
        yield
    finally:
        await _stop_cleanup()
        await close_database()


app = FastAPI(title="RAG Multimodal", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "X-Admin-Token"],
)


@app.middleware("http")
async def visitor_cookie_middleware(request: Request, call_next):
    """Resolve one validated visitor before handlers and persist it on the response."""
    settings = get_settings()
    raw = request.cookies.get(VISITOR_COOKIE_NAME)
    visitor_id = verify_visitor_cookie(raw, settings.visitor_cookie_secret) or new_visitor_id()
    signed = sign_visitor_cookie(visitor_id, settings.visitor_cookie_secret)
    request.state.visitor_id = visitor_id
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "private, no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
    if raw != signed:
        response.set_cookie(
            VISITOR_COOKIE_NAME,
            signed,
            max_age=settings.visitor_cookie_max_age_seconds,
            httponly=True,
            secure=settings.app_env.lower() == "production",
            samesite=settings.visitor_cookie_same_site,
            path="/",
        )
    return response


@app.exception_handler(AppError)
async def app_error_handler(_: Request, error: AppError) -> JSONResponse:
    """Map expected errors to safe status codes."""
    mapping = {
        FileTooLargeError: 413,
        UnsupportedFileError: 415,
        InvalidMediaError: 422,
        MediaDurationExceededError: 422,
        FileNotFoundInCatalogError: 404,
        UploadConflictError: 409,
        UploadNotCompleteError: 409,
        QuotaExceededError: 429,
        CapacityExceededError: 507,
        IngestionError: 503,
        ConfigurationError: 503,
        ExternalServiceError: 503,
    }
    status = next((code for error_type, code in mapping.items() if isinstance(error, error_type)), 400)
    headers = {"Retry-After": str(error.retry_after_seconds)} if isinstance(error, QuotaExceededError) else {}
    return JSONResponse(status_code=status, content={"detail": str(error)}, headers=headers)


@app.exception_handler(Exception)
async def unexpected_error_handler(_: Request, error: Exception) -> JSONResponse:
    """Hide internal stack traces from clients."""
    logger.error("request_failed", extra={"error_type": type(error).__name__})
    return JSONResponse(status_code=500, content={"detail": "Erro interno ao processar a solicitação."})


@app.get("/api/health", response_model=HealthStatus)
async def health() -> HealthStatus:
    """Return safe service readiness information."""
    settings = get_settings()
    database = await _database_health(settings)
    google: GoogleHealthState = "configured" if settings.google_api_key else "missing_key"
    r2 = await _r2_health(settings)
    pinecone = await _pinecone_health(settings)
    return HealthStatus(
        status=_health_status(database, google, pinecone, r2),
        services={"database": database, "r2": r2, "google": google, "gemini": google, "pinecone": pinecone},
        models={"embedding": settings.gemini_embedding_model, "generation": settings.gemini_generation_model},
        public_demo=_public_demo_config(settings),
    )


@app.get("/api/session")
async def session() -> dict[str, str]:
    """Bootstrap the persistent visitor cookie without exposing its value."""
    return {"status": "ready"}


def _public_demo_config(settings: Settings) -> PublicDemoConfig:
    """Expose only the limits needed to set safe frontend expectations."""
    return PublicDemoConfig(
        enabled=settings.public_demo_mode,
        formats=sorted(extension.upper().lstrip(".") for extension in SUPPORTED_EXTENSIONS),
        max_upload_size_mb=settings.max_upload_size_mb,
        max_daily_uploads=settings.max_daily_uploads_per_client,
        max_daily_queries=settings.max_daily_queries_per_client,
        retention_days=settings.public_retention_days,
        max_pdf_pages=settings.max_pdf_pages,
        max_audio_duration_seconds=settings.max_audio_duration_seconds,
        max_video_duration_seconds=settings.max_video_duration_seconds,
    )


async def _database_health(settings) -> DatabaseHealthState:
    """Return Postgres readiness without exposing connection details."""
    with suppress(Exception):
        return "ok" if await Catalog(settings).is_ready() else "unavailable"
    return "unavailable"


async def _pinecone_health(settings) -> PineconeHealthState:
    """Run the synchronous Pinecone probe outside the event loop."""
    with suppress(Exception):
        return await asyncio.to_thread(index_health, settings)
    return "unavailable"


async def _r2_health(settings) -> R2HealthState:
    """Check the R2 bucket without uploading or mutating an object."""
    try:
        return "ready" if await get_object_storage(settings).health_check() else "unavailable"
    except ObjectStorageConfigurationError:
        return "missing_config"
    except Exception:
        return "unavailable"


def _health_status(
    database: DatabaseHealthState, google: GoogleHealthState, pinecone: PineconeHealthState, r2: R2HealthState = "ready"
) -> HealthState:
    """Derive global readiness from all essential dependencies."""
    return (
        "offline"
        if database != "ok"
        else "ok"
        if google == "configured" and pinecone == "ready" and r2 == "ready"
        else "degraded"
    )


@app.get("/api/stats")
async def stats(request: Request) -> dict:
    """Return Postgres catalog statistics."""
    return await Catalog(get_settings()).stats(visitor_id_for_request(request))


@app.get("/api/files")
async def files(request: Request) -> dict:
    """List ready and processing files without internal paths."""
    records = await Catalog(get_settings()).list_files(visitor_id_for_request(request))
    return {"files": [_file_response(row) for row in records]}


@app.get("/api/files/{doc_id}")
async def file_status(doc_id: str, request: Request) -> dict:
    """Return one document state for upload polling."""
    record = await Catalog(get_settings()).get_file(doc_id, visitor_id_for_request(request))
    if not record:
        raise FileNotFoundInCatalogError("Arquivo nÃ£o encontrado")
    return _file_response(record)


@app.post("/api/uploads/presign", response_model=UploadPresignResponse)
async def presign_upload(request: Request, body: UploadPresignRequest) -> UploadPresignResponse:
    """Authorize one browser-to-R2 PUT without accepting a client object key."""
    settings = get_settings()
    visitor_id = visitor_id_for_request(request)
    _validate_presign_request(body, settings)
    catalog = Catalog(settings)
    existing = await catalog.find_by_sha256(body.sha256, visitor_id)
    if existing:
        return await _presign_existing(existing, body, settings, catalog)
    record = _new_upload_record(body, settings, visitor_id)
    created = await _create_presigned_document(catalog, record, request, settings, visitor_id)
    if not created.get("created"):
        return await _presign_existing(created, body, settings, catalog)
    _schedule_cleanup(settings)
    return await _presign_pending(created, settings)


@app.post("/api/uploads/{doc_id}/complete", response_model=UploadCompleteResponse)
async def complete_upload(doc_id: str, request: Request, background_tasks: BackgroundTasks) -> UploadCompleteResponse:
    """Validate the server-controlled R2 object and claim one processing worker."""
    settings = get_settings()
    visitor_id = visitor_id_for_request(request)
    catalog = Catalog(settings)
    record = await catalog.get_file(doc_id, visitor_id)
    if not record:
        raise FileNotFoundInCatalogError("Arquivo nÃ£o encontrado")
    if record["status"] in {"ready", "processing", "indexing", "failed"}:
        return _complete_response(record, True)
    if record["status"] not in {"pending_upload", "uploaded"}:
        raise InvalidMediaError("O documento nÃ£o aceita mais uploads")
    await _validate_uploaded_object(record, settings)
    await catalog.mark_uploaded(doc_id, visitor_id)
    claimed = await catalog.claim_processing(doc_id, visitor_id)
    current = await catalog.get_file(doc_id, visitor_id)
    if claimed:
        background_tasks.add_task(process_uploaded_document, doc_id, settings)
    return _complete_response(current or record, not claimed)


@app.post("/api/files/{doc_id}/retry", response_model=UploadCompleteResponse)
async def retry_file(doc_id: str, request: Request, background_tasks: BackgroundTasks) -> UploadCompleteResponse:
    """Request one explicit retry for a failed document without creating a duplicate."""
    require_admin(request)
    settings = get_settings()
    catalog = Catalog(settings)
    record = await catalog.get_file(doc_id)
    if not record:
        raise FileNotFoundInCatalogError("Arquivo não encontrado")
    if record["status"] == "failed":
        background_tasks.add_task(retry_document, doc_id, settings)
        return _complete_response({**record, "status": "processing"}, False)
    return _complete_response(record, True)


@app.post("/api/ingest", response_model=IngestResponse)
async def ingest(request: Request, file: UploadFile = File(...)) -> IngestResponse:
    """Stream, validate and index one uploaded file."""
    expected_mime = mime_for(file.filename or "")
    if file.content_type and not _mime_compatible(file.content_type, expected_mime):
        raise UnsupportedFileError("MIME type informado não corresponde à extensão")
    settings = get_settings()
    saved = await save_upload_stream(file, file.filename, settings)
    _schedule_cleanup(settings)
    try:
        result = await _run_direct_ingest(saved.path, file.filename, request, settings, visitor_id_for_request(request))
        return IngestResponse(**result.__dict__)
    finally:
        saved.path.unlink(missing_ok=True)


@app.post("/api/query", response_model=QueryResponse)
async def query(request: Request, body: QueryRequest) -> QueryResponse:
    """Retrieve evidence and generate a grounded answer off the event loop."""
    settings = get_settings()
    visitor_id = visitor_id_for_request(request)
    catalog = Catalog(settings)
    if body.conversation_id and await catalog.get_conversation(visitor_id, str(body.conversation_id)) is None:
        raise FileNotFoundInCatalogError("Conversa não encontrada")
    await _reserve_query_if_public(request, settings)
    result = await asyncio.to_thread(_run_query, body, visitor_id)
    validated = QueryResponse(**result)
    persisted = await _persist_generated_response(catalog, visitor_id, body, result)
    return QueryResponse(**(validated.model_dump() | persisted))


async def _persist_generated_response(catalog: Catalog, visitor_id: str, body: QueryRequest, result: dict[str, Any]) -> dict[str, str]:
    """Persist a valid answer before exposing its feedback identifier."""
    source_rows = [source.model_dump() for source in result["sources"]]
    persist = getattr(catalog, "persist_generated_response", None)
    if persist:
        return await persist(visitor_id, body.question, result["answer"], [source["chunk_id"] for source in source_rows], source_rows, result["insufficient_context"], str(body.conversation_id) if body.conversation_id else None)
    if not hasattr(catalog, "create_conversation"):
        return {}
    conversation_id = await catalog.create_conversation(visitor_id, str(body.conversation_id) if body.conversation_id else None)
    message_id = await catalog.record_message(visitor_id, conversation_id, body.question, result["answer"], [source["chunk_id"] for source in source_rows], source_rows, result["insufficient_context"])
    return {"conversation_id": conversation_id, "response_id": message_id, "message_id": message_id}


@app.delete("/api/files/{doc_id}")
async def delete_file(doc_id: str, request: Request) -> dict[str, Any]:
    """Delete one document and its vectors."""
    settings = get_settings()
    visitor_id = visitor_id_for_request(request)
    token = request.headers.get("X-Admin-Token", "")
    is_admin = bool(settings.admin_token) and hmac.compare_digest(token, settings.admin_token)
    if not is_admin and not await Catalog(settings).get_file(doc_id, visitor_id):
        raise FileNotFoundInCatalogError("Arquivo não encontrado")
    outcome = await delete_document_async(doc_id, settings, None if is_admin else visitor_id)
    if outcome.status == "deleting" and not outcome.claimed:
        return JSONResponse(status_code=202, content=outcome.as_dict())
    return outcome.as_dict()


@app.delete("/api/index")
async def clear(request: Request, body: ClearIndexRequest) -> dict[str, Any]:
    """Clear only the configured Pinecone namespace, R2 objects and Postgres catalog."""
    require_admin(request)
    settings = get_settings()
    result = await asyncio.to_thread(
        clear_index,
        settings,
        confirmation=body.confirmation,
        admin_token=request.headers.get("X-Admin-Token"),
    )
    return result


@app.post("/api/feedback")
async def feedback(request: Request, body: FeedbackRequest) -> dict[str, str]:
    """Persist bounded answer feedback in the Postgres catalog."""
    settings = get_settings()
    visitor_id = visitor_id_for_request(request)
    catalog = Catalog(settings)
    response_id = str(body.response_id)
    message = await catalog.get_message(visitor_id, response_id)
    if message is None:
        raise FileNotFoundInCatalogError("Resposta nÃ£o encontrada")
    feedback_id = await catalog.record_feedback(visitor_id, response_id, body.useful)
    return {"id": feedback_id}


@app.get("/api/conversations/{conversation_id}", response_model=ConversationResponse)
async def conversation(conversation_id: str, request: Request) -> ConversationResponse:
    """Load history only for the visitor that owns the conversation."""
    records = await Catalog(get_settings()).get_conversation(visitor_id_for_request(request), conversation_id)
    if records is None:
        raise FileNotFoundInCatalogError("Conversa nÃ£o encontrada")
    return ConversationResponse(conversation_id=conversation_id, messages=[ConversationMessageResponse(**record) for record in records])


@app.delete("/api/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str, request: Request) -> dict[str, bool]:
    """Delete only the current visitor's conversation history."""
    deleted = await Catalog(get_settings()).delete_conversation(visitor_id_for_request(request), conversation_id)
    if not deleted:
        raise FileNotFoundInCatalogError("Conversa nÃ£o encontrada")
    return {"deleted": True}


def _file_response(row: dict) -> dict:
    """Map one catalog record to the public polling shape."""
    return {
        "doc_id": row["doc_id"],
        "name": row["original_name"],
        "file_type": row["file_type"],
        "mime_type": row["mime_type"],
        "chunks": row["chunks_count"],
        "size_bytes": row["size_bytes"],
        "status": row["status"],
        "deletion_stage": row.get("deletion_stage"),
        "warnings": _decode_warnings(row["warnings_json"]),
        "error": row.get("safe_error_message"),
        "ingested_at": row["created_at"],
    }


def _validate_presign_request(body: UploadPresignRequest, settings) -> None:
    """Validate browser metadata against the server allowlist."""
    expected_mime = mime_for(body.file_name)
    if body.size_bytes > settings.max_upload_size_bytes:
        raise FileTooLargeError(f"Arquivo excede o limite de {settings.max_upload_size_mb} MB")
    if body.mime_type != expected_mime:
        raise UnsupportedFileError("MIME type nÃ£o corresponde Ã  extensÃ£o")


def _new_upload_record(body: UploadPresignRequest, settings, visitor_id: str | None = None) -> dict:
    """Build a pending document using only server-generated storage state."""
    doc_id = str(uuid4())
    sanitized = sanitize_filename(body.file_name)
    upload_expires_at = datetime.now(UTC) + timedelta(seconds=settings.r2_presigned_upload_ttl_seconds)
    return {
        "doc_id": doc_id,
        "original_name": body.file_name,
        "sanitized_name": sanitized,
        "object_key": _original_key(doc_id, sanitized, settings, visitor_id),
        "file_type": file_type_for(sanitized),
        "mime_type": body.mime_type,
        "sha256": body.sha256,
        "size_bytes": body.size_bytes,
        "status": "pending_upload",
        "upload_expires_at": upload_expires_at,
        "visitor_id": visitor_id,
    }


def _original_key(doc_id: str, sanitized: str, settings, visitor_id: str | None = None) -> str:
    """Build a controlled original key without accepting frontend path input."""
    return original_object_key(doc_id, sanitized, settings, visitor_id)


async def _create_presigned_document(catalog: Catalog, record: dict, request: Request, settings: Settings, visitor_id: str | None = None) -> dict:
    """Reserve daily and global upload quota before issuing an R2 URL."""
    return await catalog.create_document_with_quota(record, client_hash_for_request(request, settings, visitor_id), utc_usage_date())


async def _run_direct_ingest(path, filename: str | None, request: Request, settings: Settings, visitor_id: str | None = None):
    """Run the legacy multipart path with the same atomic public reservation."""
    return await asyncio.to_thread(
        ingest_file,
        path,
        filename,
        quota_client_hash=client_hash_for_request(request, settings, visitor_id),
        quota_usage_date=utc_usage_date(),
        visitor_id=visitor_id,
    )


async def _reserve_query_if_public(request: Request, settings: Settings) -> None:
    """Reserve every public query in Postgres so the limit survives restarts."""
    await Catalog(settings).reserve_query_quota(client_hash_for_request(request, settings, visitor_id_for_request(request)), utc_usage_date())


async def _presign_existing(
    existing: dict, body: UploadPresignRequest, settings, catalog: Catalog
) -> UploadPresignResponse:
    """Reuse a hash-owned pending document or return its current state."""
    _validate_existing_upload(existing, body)
    if existing["status"] != "pending_upload":
        return _state_presign_response(existing)
    await catalog.renew_upload(
        existing["doc_id"], datetime.now(UTC) + timedelta(seconds=settings.r2_presigned_upload_ttl_seconds), existing.get("visitor_id")
    )
    refreshed = await catalog.get_file(existing["doc_id"], existing.get("visitor_id"))
    return await _presign_pending(refreshed or existing, settings)


async def _presign_pending(record: dict, settings) -> UploadPresignResponse:
    """Generate a short-lived PUT URL with signed content type and metadata."""
    metadata = original_object_metadata(record["doc_id"], record["sha256"], record["sanitized_name"], record["mime_type"], settings, record.get("visitor_id"))
    url = await get_object_storage(settings).generate_presigned_put_url(
        record["storage_key"],
        content_type=record["mime_type"],
        metadata=metadata,
        expires_in=settings.r2_presigned_upload_ttl_seconds,
    )
    return UploadPresignResponse(
        doc_id=record["doc_id"],
        upload_url=url,
        headers=_upload_headers(record["mime_type"], metadata),
        expires_in=settings.r2_presigned_upload_ttl_seconds,
        duplicate=False,
        status="pending_upload",
    )


def _state_presign_response(record: dict) -> UploadPresignResponse:
    """Return an existing document state without exposing a new URL."""
    return UploadPresignResponse(
        doc_id=record["doc_id"], upload_url=None, headers={}, expires_in=0, duplicate=True, status=record["status"]
    )


def _upload_headers(mime_type: str, metadata: dict[str, str]) -> dict[str, str]:
    """Return the exact headers signed into the browser PUT."""
    return {"Content-Type": mime_type, **{f"x-amz-meta-{key}": value for key, value in metadata.items()}}


def _validate_existing_upload(existing: dict, body: UploadPresignRequest) -> None:
    """Reject an impossible hash reuse with different declared metadata."""
    if existing["size_bytes"] != body.size_bytes or existing["mime_type"] != body.mime_type:
        raise UploadConflictError("SHA-256 jÃ¡ associado a metadados diferentes")


async def _validate_uploaded_object(record: dict, settings) -> None:
    """HEAD R2 and compare every server-controlled upload invariant."""
    metadata = await get_object_storage(settings).head_object(record["storage_key"])
    if metadata is None:
        raise UploadNotCompleteError("O objeto ainda nÃ£o foi encontrado no R2")
    if metadata.size_bytes != record["size_bytes"]:
        raise InvalidMediaError("Tamanho do objeto no R2 nÃ£o corresponde ao declarado")
    if metadata.content_type != record["mime_type"]:
        raise UnsupportedFileError("Content-Type do objeto no R2 nÃ£o corresponde ao esperado")
    expected = original_object_metadata(record["doc_id"], record["sha256"], record["sanitized_name"], record["mime_type"], settings, record.get("visitor_id"))
    actual = metadata.metadata or {}
    if any(actual.get(key) != value for key, value in expected.items()):
        raise InvalidMediaError("Metadados do objeto no R2 nÃ£o correspondem ao upload autorizado")


def _complete_response(record: dict, duplicate: bool) -> UploadCompleteResponse:
    """Map a current document to the complete endpoint response."""
    return UploadCompleteResponse(
        doc_id=record["doc_id"],
        duplicate=duplicate,
        status=record["status"],
        chunks=record["chunks_count"],
        warnings=record["warnings"],
        error=record.get("safe_error_message"),
    )


def _run_query(request: QueryRequest, visitor_id: str | None = None) -> dict:
    """Run retrieval and generation synchronously in a worker thread."""
    settings = get_settings()
    sources = retrieve(
        request.question,
        request.top_k,
        file_type=request.filters.file_type,
        doc_id=request.filters.doc_id,
        visitor_id=visitor_id,
        settings=settings,
    )
    history = [
        GenerationHistory(item.role, item.content) for item in request.history[-settings.max_chat_history_messages :]
    ]
    answer = generate_answer(request.question, sources, history, request.answer_mode, settings)
    used_sources = [source for source in sources if source.chunk_id in answer.used_chunk_ids]
    return {
        "answer": answer.answer,
        "sources": [_source_response(source, settings, visitor_id) for source in used_sources],
        "insufficient_context": answer.insufficient_context,
    }


def _source_response(source: RetrievedSource, settings=None, visitor_id: str | None = None) -> SourceResponse:
    """Map internal source data to the safe public response."""
    media_url = _media_url(source.media_key, settings, visitor_id, source.doc_id) if source.media_key else None
    return SourceResponse(
        doc_id=source.doc_id,
        chunk_id=source.chunk_id,
        file_name=source.file_name,
        file_type=source.file_type,
        content_modality=source.content_modality,
        page_number=source.page_number,
        text_preview=source.text_preview,
        media_url=media_url,
        score=source.score,
    )


def _media_url(key: str, settings, visitor_id: str | None = None, doc_id: str | None = None) -> str | None:
    """Return a short-lived URL only after confirming the private object exists."""
    if not is_managed_object_key(key, settings, visitor_id, doc_id):
        return None
    try:
        storage = get_object_storage(settings)
        if asyncio.run(storage.head_object(key)) is None:
            return None
        return storage.generate_presigned_get_url_sync(key, expires_in=settings.r2_presigned_url_ttl_seconds)
    except Exception:
        logger.warning("source_media_url_unavailable")
        return None


def _decode_warnings(value: str) -> list[str]:
    """Decode warning JSON without exposing malformed data."""
    if isinstance(value, list):
        return [str(item) for item in value]
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []


def _mime_compatible(received: str, expected: str) -> bool:
    """Allow browser text variants while enforcing all binary MIME types."""
    return received == expected or (expected.startswith("text/") and received.startswith("text/"))
