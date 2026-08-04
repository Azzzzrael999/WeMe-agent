"""Verify registered RAG documents, Qdrant payloads, and representative retrievals."""

from __future__ import annotations

import json
import sys

from qdrant_client import QdrantClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database.engine import get_engine
from app.models.metadata import RagDocument
from app.rag.policies import QdrantPolicyRetriever

EXPECTED_DOCUMENT_COUNT = 22


def main() -> int:
    settings = get_settings()
    with Session(get_engine()) as session:
        document_count = session.scalar(select(func.count()).select_from(RagDocument)) or 0
        indexed_count = (
            session.scalar(
                select(func.count())
                .select_from(RagDocument)
                .where(RagDocument.status == "INDEXED")
            )
            or 0
        )
        registered_chunks = session.scalar(select(func.sum(RagDocument.chunk_count))) or 0

    client = QdrantClient(url=settings.qdrant_url, timeout=10)
    all_points, _ = client.scroll(
        collection_name=settings.qdrant_collection,
        limit=1000,
        with_payload=True,
    )
    document_points = [
        point
        for point in all_points
        if isinstance(point.payload, dict) and point.payload.get("documentId")
    ]
    document_ids = {
        point.payload.get("documentId")
        for point in document_points
        if isinstance(point.payload, dict)
    }
    retriever = QdrantPolicyRetriever(settings=settings, client=client)
    retriever._seeded = True
    vip_hits = retriever.search("VIP会议室普通内部会议可以使用吗", limit=5)
    architecture_hits = retriever.search("架构评审需要哪些角色和设备", limit=5)
    vip_ok = any(hit.document_id == "doc_vip_executive_room_policy" for hit in vip_hits)
    architecture_ok = any(
        hit.document_id == "doc_architecture_review_standard" for hit in architecture_hits
    )
    summary = {
        "documentCount": int(document_count),
        "indexedCount": int(indexed_count),
        "registeredChunkCount": int(registered_chunks),
        "qdrantDocumentCount": len(document_ids),
        "qdrantDocumentChunkCount": len(document_points),
        "vipRetrieval": vip_ok,
        "vipHitDocumentIds": [hit.document_id for hit in vip_hits],
        "architectureRetrieval": architecture_ok,
        "architectureHitDocumentIds": [hit.document_id for hit in architecture_hits],
    }
    print(json.dumps(summary, ensure_ascii=False))
    return (
        0
        if document_count == EXPECTED_DOCUMENT_COUNT
        and indexed_count == EXPECTED_DOCUMENT_COUNT
        and registered_chunks == len(document_points)
        and len(document_ids) == EXPECTED_DOCUMENT_COUNT
        and vip_ok
        and architecture_ok
        else 1
    )


if __name__ == "__main__":
    sys.exit(main())
