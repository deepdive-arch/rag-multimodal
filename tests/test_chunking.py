from core.config import Settings
from services.chunking import chunk_blocks, chunk_text


def test_chunking_preserves_page_and_heading():
    settings = Settings(chunk_size=40, chunk_overlap=10, min_chunk_size=5)
    chunks = chunk_text("Primeiro parágrafo.\n\nSegundo parágrafo.", 4, "Contrato", settings)
    assert chunks
    assert all(chunk.page_number == 4 for chunk in chunks)
    assert all("Contrato" in chunk.text for chunk in chunks)


def test_oversized_block_uses_overlap():
    settings = Settings(chunk_size=20, chunk_overlap=5, min_chunk_size=3)
    chunks = chunk_text("abcdefghijklmnopqrstuvwx", settings=settings)
    assert len(chunks) > 1
    assert chunks[0].text[-5:] == chunks[1].text[:5]


def test_empty_chunks_are_discarded():
    settings = Settings(min_chunk_size=3)
    assert chunk_blocks([], settings) == []
