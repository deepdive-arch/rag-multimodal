"""Bounded, idempotent cleanup CLI for retention and pending deletions."""

import argparse
import asyncio
import json
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import get_settings  # noqa: E402
from services.deletion import cleanup_expired_documents  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    """Build the cleanup argument parser."""
    parser = argparse.ArgumentParser(description="Cleanup expired RAG documents with retryable deletion")
    parser.add_argument("--dry-run", action="store_true", help="list candidates without mutating anything")
    parser.add_argument("--limit", type=int, default=2)
    parser.add_argument("--older-than", type=float, default=0, help="minimum age in days")
    return parser


async def run(*, dry_run: bool, limit: int, older_than_days: float) -> list[dict]:
    """Run one bounded cleanup batch."""
    if limit <= 0 or older_than_days < 0:
        raise ValueError("limit must be positive and older-than cannot be negative")
    return await cleanup_expired_documents(
        get_settings(), limit=limit, older_than=timedelta(days=older_than_days), dry_run=dry_run
    )


def main() -> None:
    """Parse arguments and print JSON results."""
    args = _parser().parse_args()
    result = asyncio.run(run(dry_run=args.dry_run, limit=args.limit, older_than_days=args.older_than))
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
