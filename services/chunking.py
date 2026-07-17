"""Semantic-first text chunking helpers."""

import re
from dataclasses import dataclass

from core.config import Settings, get_settings


@dataclass(frozen=True)
class TextBlock:
    """A semantic block with optional page and heading context."""

    text: str
    page_number: int = 0
    heading: str = ""


@dataclass(frozen=True)
class ChunkDraft:
    """A chunk ready for embedding."""

    text: str
    page_number: int = 0
    heading: str = ""


def normalize_text(text: str) -> str:
    """Normalize newlines and excessive blank lines."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\n{3,}", "\n\n", normalized).strip()


def chunk_text(text: str, page_number: int = 0, heading: str = "", settings: Settings | None = None) -> list[ChunkDraft]:
    """Chunk one text region using semantic blocks and overlap windows."""
    settings = settings or get_settings()
    blocks = [TextBlock(block.strip(), page_number, heading) for block in re.split(r"\n\s*\n", normalize_text(text)) if block.strip()]
    return chunk_blocks(blocks, settings)


def chunk_blocks(blocks: list[TextBlock], settings: Settings | None = None) -> list[ChunkDraft]:
    """Combine semantic blocks and split oversized blocks with overlap."""
    settings = settings or get_settings()
    chunks: list[ChunkDraft] = []
    current: list[str] = []
    current_page = 0
    current_heading = ""
    for block in blocks:
        text = f"{block.heading}\n{block.text}".strip() if block.heading else block.text
        if len(text) > settings.chunk_size:
            chunks.extend(_split_oversized(text, block.page_number, block.heading, settings))
            current, current_page, current_heading = [], 0, ""
            continue
        candidate = "\n\n".join([*current, text]).strip()
        if current and len(candidate) > settings.chunk_size:
            chunks.append(ChunkDraft("\n\n".join(current).strip(), current_page, current_heading))
            current = [text]
            current_page = block.page_number
            current_heading = block.heading
        else:
            current.append(text)
            current_page = current_page or block.page_number
            current_heading = current_heading or block.heading
    if current:
        chunks.append(ChunkDraft("\n\n".join(current).strip(), current_page, current_heading))
    return _merge_tiny_chunks([chunk for chunk in chunks if chunk.text], settings)


def _split_oversized(text: str, page_number: int, heading: str, settings: Settings) -> list[ChunkDraft]:
    """Split an oversized region with a sliding character window."""
    step = max(1, settings.chunk_size - settings.chunk_overlap)
    return [ChunkDraft(text[start : start + settings.chunk_size], page_number, heading) for start in range(0, len(text), step) if text[start : start + settings.chunk_size].strip()]


def _merge_tiny_chunks(chunks: list[ChunkDraft], settings: Settings) -> list[ChunkDraft]:
    """Merge undersized trailing chunks whenever possible."""
    if len(chunks) < 2:
        return chunks
    result: list[ChunkDraft] = []
    for chunk in chunks:
        if result and len(chunk.text) < settings.min_chunk_size:
            previous = result.pop()
            result.append(ChunkDraft(f"{previous.text}\n\n{chunk.text}", previous.page_number, previous.heading))
        else:
            result.append(chunk)
    return result
