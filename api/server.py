"""FastAPI HTTP surface for the multimodal RAG MVP."""

import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from api.dependencies import require_admin
from api.schemas import ClearIndexRequest, FeedbackRequest, IngestResponse, QueryRequest, QueryResponse, SourceResponse
from core.config import get_settings
from core.exceptions import AppError, ConfigurationError, FileNotFoundInCatalogError, FileTooLargeError, InvalidMediaError, MediaDurationExceededError, UnsupportedFileError
from core.logging import configure_logging
from db.catalog import Catalog
from services.generation import ChatHistoryMessage as GenerationHistory, generate_answer
from services.ingestion import clear_index, delete_document, ingest_file
from services.pinecone_service import index_health
from services.retrieval import RetrievedSource, retrieve
from services.storage import mime_for, save_upload_stream


logger = logging.getLogger("rag_multimodal")


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Initialize the local catalog before serving requests."""
    configure_logging()
    await Catalog(get_settings().database_path).initialize()
    yield


app = FastAPI(title="RAG Multimodal", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[get_settings().frontend_origin], allow_credentials=False, allow_methods=["GET", "POST", "DELETE"], allow_headers=["Content-Type", "X-Admin-Token"])
app.mount("/uploads", StaticFiles(directory=get_settings().uploads_dir), name="uploads")


@app.exception_handler(AppError)
async def app_error_handler(_: Request, error: AppError) -> JSONResponse:
    """Map expected errors to safe status codes."""
    mapping = {FileTooLargeError: 413, UnsupportedFileError: 415, InvalidMediaError: 422, MediaDurationExceededError: 422, FileNotFoundInCatalogError: 404, ConfigurationError: 503}
    status = next((code for error_type, code in mapping.items() if isinstance(error, error_type)), 400)
    return JSONResponse(status_code=status, content={"detail": str(error)})


@app.exception_handler(Exception)
async def unexpected_error_handler(_: Request, error: Exception) -> JSONResponse:
    """Hide internal stack traces from clients."""
    logger.exception("request_failed", exc_info=error)
    return JSONResponse(status_code=500, content={"detail": "Erro interno ao processar a solicitação."})


@app.get("/api/health")
async def health() -> dict:
    """Return safe service readiness information."""
    settings = get_settings()
    database = await Catalog(settings.database_path).is_ready()
    return {"status": "ok" if database else "degraded", "services": {"google_configured": bool(settings.google_api_key), "pinecone_configured": bool(settings.pinecone_api_key), "database": "ok" if database else "unavailable", "pinecone_index": index_health(settings)}, "models": {"embedding": settings.gemini_embedding_model, "generation": settings.gemini_generation_model}}


@app.get("/api/stats")
async def stats() -> dict:
    """Return local catalog statistics."""
    return await Catalog(get_settings().database_path).stats()


@app.get("/api/files")
async def files() -> dict:
    """List ready and processing files without internal paths."""
    records = await Catalog(get_settings().database_path).list_files()
    return {"files": [{"doc_id": row["doc_id"], "name": row["original_name"], "file_type": row["file_type"], "mime_type": row["mime_type"], "chunks": row["chunks_count"], "size_bytes": row["size_bytes"], "status": row["status"], "warnings": _decode_warnings(row["warnings_json"]), "ingested_at": row["created_at"]} for row in records]}


@app.post("/api/ingest", response_model=IngestResponse)
async def ingest(file: UploadFile = File(...)) -> IngestResponse:
    """Stream, validate and index one uploaded file."""
    expected_mime = mime_for(file.filename or "")
    if file.content_type and not _mime_compatible(file.content_type, expected_mime):
        raise UnsupportedFileError("MIME type informado não corresponde à extensão")
    saved = await save_upload_stream(file, file.filename, get_settings())
    result = await asyncio.to_thread(ingest_file, saved.path, file.filename)
    return IngestResponse(**result.__dict__)


@app.post("/api/query", response_model=QueryResponse)
async def query(request: QueryRequest) -> QueryResponse:
    """Retrieve evidence and generate a grounded answer off the event loop."""
    result = await asyncio.to_thread(_run_query, request)
    return QueryResponse(**result)


@app.delete("/api/files/{doc_id}")
async def delete_file(doc_id: str, request: Request) -> dict[str, str]:
    """Delete one document and its vectors."""
    require_admin(request)
    await asyncio.to_thread(delete_document, doc_id)
    return {"status": "deleted"}


@app.delete("/api/index")
async def clear(request: Request, body: ClearIndexRequest) -> dict[str, str]:
    """Clear only the configured Pinecone namespace and local catalog."""
    require_admin(request)
    await asyncio.to_thread(clear_index)
    return {"status": "cleared"}


@app.post("/api/feedback")
async def feedback(body: FeedbackRequest) -> dict[str, str]:
    """Persist answer feedback locally."""
    feedback_id = await Catalog(get_settings().database_path).record_feedback(body.question, body.answer, body.useful, body.source_ids)
    return {"id": feedback_id}


def _run_query(request: QueryRequest) -> dict:
    """Run retrieval and generation synchronously in a worker thread."""
    settings = get_settings()
    sources = retrieve(request.question, request.top_k, file_type=request.filters.file_type, doc_id=request.filters.doc_id, settings=settings)
    history = [GenerationHistory(item.role, item.content) for item in request.history[-settings.max_chat_history_messages :]]
    answer = generate_answer(request.question, sources, history, request.answer_mode, settings)
    used_sources = [source for source in sources if source.chunk_id in answer.used_chunk_ids]
    return {"answer": answer.answer, "sources": [_source_response(source) for source in used_sources], "insufficient_context": answer.insufficient_context}


def _source_response(source: RetrievedSource) -> SourceResponse:
    """Map internal source data to the safe public response."""
    media_url = f"/uploads/{source.media_key.removeprefix('uploads/')}" if source.media_key else None
    return SourceResponse(doc_id=source.doc_id, chunk_id=source.chunk_id, file_name=source.file_name, file_type=source.file_type, content_modality=source.content_modality, page_number=source.page_number, text_preview=source.text_preview, media_url=media_url, score=source.score)


def _decode_warnings(value: str) -> list[str]:
    """Decode warning JSON without exposing malformed data."""
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []


def _mime_compatible(received: str, expected: str) -> bool:
    """Allow browser text variants while enforcing all binary MIME types."""
    return received == expected or (expected.startswith("text/") and received.startswith("text/"))
