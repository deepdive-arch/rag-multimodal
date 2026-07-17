"""CLI wrapper for recursive file ingestion."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.ingestion import ingest_file  # noqa: E402
from services.storage import SUPPORTED_EXTENSIONS  # noqa: E402


def main() -> None:
    """Ingest one file or every supported file under a directory."""
    parser = argparse.ArgumentParser(description="Ingest files into the multimodal RAG index")
    parser.add_argument("path", type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    paths = [args.path] if args.path.is_file() else sorted(item for item in args.path.rglob("*") if item.is_file() and item.suffix.lower() in SUPPORTED_EXTENSIONS)
    print(f"Arquivos encontrados: {len(paths)}")
    for path in paths:
        try:
            result = ingest_file(path, path.name, force=args.force)
            label = "duplicado" if result.duplicate else "ok"
            print(f"[{label}] {result.name}: {result.chunks} chunks")
            for warning in result.warnings:
                print(f"  aviso: {warning}")
        except Exception as error:
            print(f"[falha] {path.name}: {error}")


if __name__ == "__main__":
    main()
