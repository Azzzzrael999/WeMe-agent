"""CLI entry point for meeting-policy corpus ingestion."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from app.config import get_settings
from app.database.engine import get_engine
from app.rag.ingestion import (
    RagDocumentRepository,
    RagIngestionError,
    RagIngestionService,
    build_vector_index,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Index meeting policy Markdown and text PDFs")
    parser.add_argument("--source-dir", help="Directory containing .md/.pdf policy documents")
    args = parser.parse_args(argv)
    settings = get_settings()
    source_dir = Path(args.source_dir or settings.rag_source_dir)
    service = RagIngestionService(
        repository=RagDocumentRepository(get_engine()),
        vector_index=build_vector_index(settings),
    )
    try:
        results = service.ingest_directory(source_dir)
    except RagIngestionError as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc)}, ensure_ascii=False))
        return 1
    summary = {
        "status": "SUCCESS",
        "sourceDir": str(source_dir.resolve()),
        "documentCount": len(results),
        "indexedCount": sum(result.status == "INDEXED" for result in results),
        "skippedCount": sum(result.status == "SKIPPED_DUPLICATE" for result in results),
        "chunkCount": sum(result.chunk_count for result in results),
        "documents": [asdict(result) for result in results],
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
