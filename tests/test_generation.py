from pathlib import Path
from unittest.mock import MagicMock

import pytest
from google.genai import types

from api.schemas import QueryRequest
from core.config import Settings
from core.exceptions import ExternalServiceError
from services import generation
from services.generation import GeneratedAnswer, _build_contents, generate_answer
from services.retrieval import RetrievedSource


def make_settings(tmp_path: Path, media_mb: int = 1) -> Settings:
    return Settings(
        temp_processing_dir=tmp_path / "processing",
        max_upload_size_mb=max(media_mb + 1, 2),
        max_media_context_size_mb=media_mb,
    )


@pytest.fixture(autouse=True)
def local_media_fixture(monkeypatch):
    """Keep unit media bytes local without restoring a production local fallback."""
    monkeypatch.setattr("services.generation.is_managed_object_key", lambda _key, _settings: False)
    monkeypatch.setattr(
        "services.generation._media_path_and_size",
        lambda source, settings: _local_media_path(source, settings),
    )


def _local_media_path(source: RetrievedSource, settings: Settings):
    """Resolve the test fixture by basename only."""
    path = settings.temp_processing_dir / Path(source.media_key).name
    return (path, path.stat().st_size) if path.is_file() else (None, 0)


def make_source(
    chunk_id: str,
    *,
    media_key: str = "",
    content_modality: str = "image",
    chunk_text: str = "",
    file_type: str = "image",
) -> RetrievedSource:
    return RetrievedSource(
        "doc",
        chunk_id,
        f"{chunk_id}.bin",
        "stored",
        file_type,
        "image/png" if media_key else "text/plain",
        content_modality,
        0,
        chunk_text,
        chunk_text,
        media_key,
        0.0,
        0.9,
    )


def write_media(settings: Settings, name: str, size: int = 1) -> str:
    path = settings.temp_processing_dir / name
    with path.open("wb") as stream:
        stream.truncate(size)
    return f"rag/test/default/documents/doc/derived/{name}"


def test_one_media_below_budget_is_included(tmp_path: Path):
    settings = make_settings(tmp_path)
    media_key = write_media(settings, "image.png", 8)
    contents, used = _build_contents("pergunta", [make_source("a", media_key=media_key)], [], "detailed", settings)
    assert used == ["a"]
    assert isinstance(contents[2], types.Part)


def test_two_media_below_budget_are_included(tmp_path: Path):
    settings = make_settings(tmp_path)
    sources = [
        make_source("a", media_key=write_media(settings, "a.png")),
        make_source("b", media_key=write_media(settings, "b.png")),
    ]
    _, used = _build_contents("pergunta", sources, [], "detailed", settings)
    assert used == ["a", "b"]


def test_media_over_budget_is_skipped_before_read_bytes(tmp_path: Path, monkeypatch):
    settings = make_settings(tmp_path)
    media_key = write_media(settings, "large.png", settings.max_media_context_size_bytes + 1)
    monkeypatch.setattr(Path, "read_bytes", lambda _path: pytest.fail("media over budget must not be read"))
    contents, used = _build_contents("pergunta", [make_source("large", media_key=media_key)], [], "detailed", settings)
    assert used == []
    assert len(contents) == 1


def test_aggregate_budget_is_sixty_megabytes(tmp_path: Path, monkeypatch):
    settings = make_settings(tmp_path, 60)
    first = write_media(settings, "first.png", 40 * 1024 * 1024)
    second = write_media(settings, "second.png", 25 * 1024 * 1024)
    monkeypatch.setattr(Path, "read_bytes", lambda _path: b"x")
    _, used = _build_contents(
        "pergunta",
        [make_source("first", media_key=first), make_source("second", media_key=second)],
        [],
        "detailed",
        settings,
    )
    assert used == ["first"]


def test_media_part_count_is_enforced(tmp_path: Path, monkeypatch):
    settings = make_settings(tmp_path)
    sources = [make_source(str(index), media_key=write_media(settings, f"{index}.png")) for index in range(4)]
    monkeypatch.setattr(Path, "read_bytes", lambda _path: b"x")
    contents, used = _build_contents("pergunta", sources, [], "detailed", settings)
    assert used == ["0", "1", "2"]
    assert sum(isinstance(item, types.Part) for item in contents) == 3


def test_markers_and_parts_are_interleaved(tmp_path: Path):
    settings = make_settings(tmp_path)
    sources = [
        make_source("image", media_key=write_media(settings, "image.png")),
        make_source("text", content_modality="text", chunk_text="texto", file_type="text"),
    ]
    contents, used = _build_contents("pergunta", sources, [], "detailed", settings)
    assert used == ["image", "text"]
    assert "[Fonte 1]" in contents[1]
    assert isinstance(contents[2], types.Part)
    assert "[Fonte 2]" in contents[3]


def test_missing_media_is_not_marked_used(tmp_path: Path):
    settings = make_settings(tmp_path)
    _, used = _build_contents(
        "pergunta", [make_source("missing", media_key="uploads/missing.png")], [], "detailed", settings
    )
    assert used == []


def test_skipped_media_does_not_consume_source_number(tmp_path: Path):
    settings = make_settings(tmp_path)
    source = make_source("text", content_modality="text", chunk_text="conteúdo", file_type="text")
    contents, used = _build_contents(
        "pergunta", [make_source("missing", media_key="uploads/missing.png"), source], [], "detailed", settings
    )
    assert used == ["text"]
    assert "[Fonte 1]" in contents[1]


def test_no_usable_source_returns_insufficient_without_gemini(tmp_path: Path, monkeypatch):
    settings = make_settings(tmp_path)
    monkeypatch.setattr("services.generation.get_google_client", lambda: pytest.fail("Gemini must not be called"))
    answer = generate_answer(
        "pergunta", [make_source("missing", media_key="uploads/missing.png")], [], "detailed", settings
    )
    assert answer.insufficient_context is True
    assert answer.used_chunk_ids == []


def test_text_source_is_included_normally(tmp_path: Path):
    settings = make_settings(tmp_path)
    _, used = _build_contents(
        "pergunta",
        [make_source("text", content_modality="text", chunk_text="conteúdo", file_type="text")],
        [],
        "detailed",
        settings,
    )
    assert used == ["text"]


def test_generation_reserves_output_tokens_and_limits_gemini_thinking(tmp_path: Path, monkeypatch):
    settings = make_settings(tmp_path)
    settings.google_api_key = "test-key"
    captured = {}

    class FakeModels:
        def generate_content(self, **kwargs):
            captured.update(kwargs)
            return types.GenerateContentResponse(
                candidates=[
                    types.Candidate(
                        content=types.Content(parts=[types.Part(text="resposta completa")]), finish_reason="STOP"
                    )
                ]
            )

    class FakeClient:
        models = FakeModels()

    monkeypatch.setattr("services.generation.get_google_client", lambda: FakeClient())
    answer = generate_answer(
        "pergunta",
        [make_source("text", content_modality="text", chunk_text="conteúdo", file_type="text")],
        [],
        "detailed",
        settings,
    )

    assert answer.answer == "resposta completa"
    assert captured["config"].max_output_tokens == 4096
    assert captured["config"].thinking_config.thinking_level.value == "LOW"
    assert captured["config"].thinking_config.include_thoughts is False


def test_generation_does_not_return_token_limited_partial_answer(tmp_path: Path, monkeypatch):
    settings = make_settings(tmp_path)
    settings.google_api_key = "test-key"

    class FakeModels:
        def generate_content(self, **kwargs):
            return types.GenerateContentResponse(
                candidates=[
                    types.Candidate(
                        content=types.Content(parts=[types.Part(text="resposta pela metade")]),
                        finish_reason="MAX_TOKENS",
                    )
                ]
            )

    class FakeClient:
        models = FakeModels()

    monkeypatch.setattr("services.generation.get_google_client", lambda: FakeClient())
    with pytest.raises(ExternalServiceError, match="limite de saída"):
        generate_answer(
            "pergunta",
            [make_source("text", content_modality="text", chunk_text="conteúdo", file_type="text")],
            [],
            "detailed",
            settings,
        )


def test_generation_retry_recovers_from_temporary_provider_failure(monkeypatch):
    operation = MagicMock(side_effect=[OSError("temporary"), OSError("temporary"), "complete"])
    monkeypatch.setattr(generation.time, "sleep", lambda _seconds: None)

    result = generation._with_retry(operation)

    assert result == "complete"
    assert operation.call_count == 3


def test_api_returns_only_sources_used_by_generation(monkeypatch, tmp_path: Path):
    import api.server as server

    settings = make_settings(tmp_path)
    sources = [
        make_source("a", content_modality="text", chunk_text="a", file_type="text"),
        make_source("b", content_modality="text", chunk_text="b", file_type="text"),
    ]
    monkeypatch.setattr(server, "get_settings", lambda: settings)
    monkeypatch.setattr(server, "retrieve", lambda *args, **kwargs: sources)
    monkeypatch.setattr(server, "generate_answer", lambda *args, **kwargs: GeneratedAnswer("answer", False, ["b"]))
    result = server._run_query(QueryRequest(question="pergunta"))
    assert [source.chunk_id for source in result["sources"]] == ["b"]


def test_api_preserves_media_url_for_used_source(monkeypatch, tmp_path: Path):
    import api.server as server

    settings = make_settings(tmp_path)
    source = make_source("media", media_key=write_media(settings, "media.png"))
    monkeypatch.setattr(server, "get_settings", lambda: settings)
    monkeypatch.setattr(server, "retrieve", lambda *args, **kwargs: [source])
    monkeypatch.setattr(server, "generate_answer", lambda *args, **kwargs: GeneratedAnswer("answer", False, ["media"]))
    result = server._run_query(QueryRequest(question="pergunta"))
    assert result["sources"][0].media_url is None


def test_cli_returns_only_sources_used_by_generation(monkeypatch, tmp_path: Path):
    import tools.query_rag as cli

    settings = make_settings(tmp_path)
    sources = [
        make_source("a", content_modality="text", chunk_text="a", file_type="text"),
        make_source("b", content_modality="text", chunk_text="b", file_type="text"),
    ]
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "retrieve", lambda *args, **kwargs: sources)
    monkeypatch.setattr(cli, "generate_answer", lambda *args, **kwargs: GeneratedAnswer("answer", False, ["b"]))
    result = cli.query("pergunta")
    assert [source["chunk_id"] for source in result["sources"]] == ["b"]
