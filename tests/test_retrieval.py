from types import SimpleNamespace

from core.config import Settings
from core.visitor import new_visitor_id
from services import retrieval


def test_retrieval_prefix_filters_and_diversity(monkeypatch, tmp_path):
    visitor_id = new_visitor_id()
    settings = Settings(
        _env_file=None,
        database_url="",
        temp_processing_dir=tmp_path / "processing",
        min_relevance_score=0.2,
    )
    captured = {}
    monkeypatch.setattr(retrieval, "embed_text", lambda text, settings: [0.0] * settings.embedding_dimension)
    monkeypatch.setattr(
        retrieval,
        "query_vectors",
        lambda vector, top_k, metadata_filter, settings, visitor: (
            captured.update({"filter": metadata_filter})
            or SimpleNamespace(
                matches=[
                    SimpleNamespace(
                        score=0.9,
                        metadata={
                            "doc_id": "a",
                            "chunk_id": "a1",
                            "original_name": "a.txt",
                            "file_type": "text",
                            "content_modality": "text",
                            "chunk_text": "full",
                            "text_preview": "full",
                            "visitor_id": visitor_id,
                        },
                    ),
                    SimpleNamespace(
                        score=0.8,
                        metadata={
                            "doc_id": "b",
                            "chunk_id": "b1",
                            "original_name": "b.txt",
                            "file_type": "text",
                            "content_modality": "text",
                            "chunk_text": "full",
                            "text_preview": "full",
                            "visitor_id": visitor_id,
                        },
                    ),
                ]
            )
        ),
    )
    sources = retrieval.retrieve("pergunta", 2, file_type="text", doc_id="a", visitor_id=visitor_id, settings=settings)
    assert captured["filter"] == {"$and": [{"visitor_id": {"$eq": visitor_id}}, {"file_type": {"$eq": "text"}}, {"doc_id": {"$eq": "a"}}]}
    assert len(sources) == 2


def test_retrieval_deduplicates_chunk_sources(monkeypatch, tmp_path):
    visitor_id = new_visitor_id()
    settings = Settings(
        _env_file=None,
        database_url="",
        temp_processing_dir=tmp_path / "processing",
        min_relevance_score=0.2,
    )
    match = SimpleNamespace(
        score=0.9,
        metadata={
            "doc_id": "a",
            "chunk_id": "a1",
            "original_name": "a.txt",
            "file_type": "text",
            "content_modality": "text",
            "chunk_text": "full",
            "text_preview": "full",
            "visitor_id": visitor_id,
        },
    )
    monkeypatch.setattr(retrieval, "embed_text", lambda text, settings: [0.0] * settings.embedding_dimension)
    monkeypatch.setattr(
        retrieval,
        "query_vectors",
        lambda vector, top_k, metadata_filter, settings, visitor: SimpleNamespace(matches=[match, match]),
    )

    sources = retrieval.retrieve("pergunta", 2, visitor_id=visitor_id, settings=settings)

    assert [source.chunk_id for source in sources] == ["a1"]
