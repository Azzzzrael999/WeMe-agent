from __future__ import annotations

import base64
from collections.abc import Iterator

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from qdrant_client import QdrantClient, models
from sqlalchemy import Engine, create_engine
from sqlalchemy.pool import StaticPool

from app.api.internal import get_agent_context
from app.api.knowledge import _invoke, get_management_service, router
from app.database.base import Base
from app.rag.ingestion import QdrantVectorIndex, RagDocumentRepository, RagIngestionService
from app.rag.management import (
    RagDocumentConflictError,
    RagDocumentInvalidError,
    RagDocumentManagementService,
    RagDocumentUnavailableError,
)
from app.security import AgentContext


@pytest.fixture
def rag_engine() -> Iterator[Engine]:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def managed_service(rag_engine: Engine) -> tuple[RagDocumentManagementService, QdrantClient]:
    client = QdrantClient(":memory:")
    service = RagDocumentManagementService(
        engine=rag_engine,
        vector_index=QdrantVectorIndex(
            url="http://unused", collection_name="managed_policies", client=client
        ),
    )
    return service, client


def _markdown(*, body: str, version: str = "1.0") -> str:
    return f"""---
documentId: doc_managed_policy
title: 在线维护制度
documentType: MEETING_POLICY
department: ALL
version: {version}
effectiveDate: 2026-08-01
status: ACTIVE
priority: 180
timezone: Asia/Shanghai
---
# 在线维护制度
## 规则正文
{body}
"""


def _upload(service: RagDocumentManagementService, content: str):  # type: ignore[no-untyped-def]
    return service.upload(
        file_name="managed.md",
        media_type="text/markdown",
        content_base64=base64.b64encode(content.encode("utf-8")).decode("ascii"),
        metadata=None,
    )


def test_admin_management_replaces_chunks_and_preserves_delete_tombstone(
    tmp_path, managed_service: tuple[RagDocumentManagementService, QdrantClient]
) -> None:
    service, client = managed_service
    created = _upload(service, _markdown(body="会议开始前必须确认议程。"))

    assert created.status == "INDEXED"
    assert created.record_version == 0
    assert service.list_documents(keyword="在线", document_type=None, page=1, size=20).total == 1
    assert "必须确认议程" in service.get_document(created.document_id).content_text

    updated = service.update_markdown(
        document_id=created.document_id,
        content=_markdown(body="会议开始前必须确认议程，并检查必需材料。", version="1.1"),
        expected_record_version=0,
    )
    assert updated.record_version == 1
    assert updated.version == "1.1"
    with pytest.raises(RagDocumentConflictError):
        service.update_markdown(
            document_id=created.document_id,
            content=_markdown(body="过期覆盖。", version="1.2"),
            expected_record_version=0,
        )

    points, _ = client.scroll(
        collection_name="managed_policies",
        scroll_filter=models.Filter(
            must=[
                models.FieldCondition(
                    key="documentId", match=models.MatchValue(value=created.document_id)
                )
            ]
        ),
        limit=100,
        with_payload=True,
    )
    assert points
    assert all("检查必需材料" in str(point.payload) for point in points)

    deleted = service.delete(document_id=created.document_id, expected_record_version=1)
    assert deleted.status == "DELETED"
    assert deleted.record_version == 2
    assert service.list_documents(keyword=None, document_type=None, page=1, size=20).total == 0

    source = tmp_path / "managed.md"
    source.write_text(_markdown(body="会议开始前必须确认议程。"), encoding="utf-8")
    cli_result = RagIngestionService(
        RagDocumentRepository(service.engine), service.vector_index
    ).ingest_file(source)
    assert cli_result.status == "SKIPPED_DELETED"

    restored = _upload(service, _markdown(body="管理员显式恢复制度。", version="2.0"))
    assert restored.status == "INDEXED"
    assert restored.record_version == 3
    assert "显式恢复" in restored.content_text


def test_internal_api_allows_employee_reads_but_rejects_employee_upload(
    managed_service: tuple[RagDocumentManagementService, QdrantClient]
) -> None:
    service, _ = managed_service
    _upload(service, _markdown(body="员工可以阅读。"))
    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[get_management_service] = lambda: service
    application.dependency_overrides[get_agent_context] = lambda: AgentContext(
        user_id=1001,
        roles=("EMPLOYEE",),
        trace_id="trc_test",
        run_id="rag_test",
        token="safe-test-token",
    )

    with TestClient(application) as client:
        response = client.get("/internal/v1/knowledge-documents")
        forbidden = client.post(
            "/internal/v1/knowledge-documents",
            json={
                "fileName": "other.md",
                "mediaType": "text/markdown",
                "contentBase64": base64.b64encode(_markdown(body="禁止写入").encode()).decode(),
            },
        )

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert forbidden.status_code == 403


def test_internal_api_maps_vector_dependency_failure_to_service_unavailable() -> None:
    def fail() -> None:
        raise RagDocumentUnavailableError("Qdrant document replacement failed")

    with pytest.raises(HTTPException) as captured:
        _invoke(fail)

    assert getattr(captured.value, "status_code", None) == 503
    assert getattr(captured.value, "detail", None) == "AGENT_UNAVAILABLE"


def test_upload_rejects_source_above_managed_content_limit(
    managed_service: tuple[RagDocumentManagementService, QdrantClient],
) -> None:
    service, _ = managed_service

    with pytest.raises(RagDocumentInvalidError):
        _upload(service, _markdown(body="x" * 500_000))
