"""CLI wrapper for retrieval and grounded generation."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import get_settings  # noqa: E402
from services.generation import ChatHistoryMessage, generate_answer  # noqa: E402
from services.retrieval import retrieve  # noqa: E402


def query(question: str, top_k: int = 5, history: list[dict] | None = None, file_type: str | None = None, doc_id: str | None = None, answer_mode: str = "detailed") -> dict:
    """Retrieve sources and generate one answer."""
    settings = get_settings()
    sources = retrieve(question, top_k, file_type=file_type, doc_id=doc_id, settings=settings)
    messages = [ChatHistoryMessage(item["role"], item["content"]) for item in (history or [])[-settings.max_chat_history_messages :]]
    answer = generate_answer(question, sources, messages, answer_mode, settings)
    used_sources = [source for source in sources if source.chunk_id in answer.used_chunk_ids]
    return {"answer": answer.answer, "sources": [source.__dict__ for source in used_sources], "insufficient_context": answer.insufficient_context}


def main() -> None:
    """Parse and run the query command."""
    parser = argparse.ArgumentParser(description="Query the multimodal RAG index")
    parser.add_argument("question")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--file-type")
    parser.add_argument("--doc-id")
    parser.add_argument("--mode", choices=["quick", "detailed", "evidence"], default="detailed")
    args = parser.parse_args()
    print(query(args.question, args.top_k, file_type=args.file_type, doc_id=args.doc_id, answer_mode=args.mode)["answer"])


if __name__ == "__main__":
    main()
