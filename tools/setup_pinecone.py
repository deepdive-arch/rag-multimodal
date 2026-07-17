"""Create or validate the configured Pinecone index."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.logging import configure_logging  # noqa: E402
from services.pinecone_service import setup_index  # noqa: E402


def main() -> None:
    """Run the idempotent setup command."""
    configure_logging()
    print(setup_index())


if __name__ == "__main__":
    main()
