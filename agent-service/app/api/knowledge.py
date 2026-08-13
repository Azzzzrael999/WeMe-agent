"""Internal knowledge-document APIs consumed by the Java public gateway."""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache
from typing import Annotated, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.internal import get_agent_context
from app.config import get_settings
from app.database.engine import get_engine
from app.models.metadata import RagDocument
from app.rag.ingestion import DocumentMetadata, QdrantVectorIndex
from app.rag.management import (
    RagDocumentConflictError,
    RagDocumentInvalidError,
    RagDocumentManagementService,
    RagDocumentNotFoundError,
    RagDocumentUnavailableError,
)
from app.security import AgentContext

router = APIRouter(prefix="/internal/v1/knowledge-documents", tags=["knowledge-documents"])
ResultT = TypeVar("ResultT")


def _to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)


class KnowledgeDocumentUploadRequest(ApiModel):
    file_name: str = Field(min_length=1, max_length=255)
    media_type: str
    content_base64: str = Field(min_length=1)
    metadata: DocumentMetadata | None = None


class KnowledgeDocumentUpdateRequest(ApiModel):
    content: str = Field(min_length=1, max_length=500_000)
    expected_version: int = Field(ge=0)


class KnowledgeDocumentItem(ApiModel):
    document_id: str
    title: str
    document_type: str
    department: str
    version: str
    effective_date: str
    priority: int
    file_name: str
    media_type: str
    status: str
    chunk_count: int
    checksum: str
    record_version: int
    created_at: str
    updated_at: str
    indexed_at: str | None
    editable: bool

    @classmethod
    def from_record(cls, record: RagDocument) -> KnowledgeDocumentItem:
        return cls(
            document_id=record.document_id,
            title=record.title,
            document_type=record.document_type,
            department=record.department,
            version=record.version,
            effective_date=record.effective_date.isoformat(),
            priority=record.priority,
            file_name=record.file_name,
            media_type=record.media_type,
            status=record.status,
            chunk_count=record.chunk_count,
            checksum=record.checksum,
            record_version=record.record_version,
            created_at=record.created_at.isoformat(),
            updated_at=record.updated_at.isoformat(),
            indexed_at=record.indexed_at.isoformat() if record.indexed_at else None,
            editable=record.media_type == "text/markdown",
        )


class KnowledgeDocumentDetail(KnowledgeDocumentItem):
    content: str

    @classmethod
    def from_record(cls, record: RagDocument) -> KnowledgeDocumentDetail:
        item = KnowledgeDocumentItem.from_record(record)
        return cls(**item.model_dump(), content=record.content_text)


class KnowledgeDocumentList(ApiModel):
    items: list[KnowledgeDocumentItem]
    total: int


class KnowledgeDocumentDeleteResult(ApiModel):
    document_id: str
    status: str
    record_version: int


@lru_cache
def get_management_service() -> RagDocumentManagementService:
    settings = get_settings()
    return RagDocumentManagementService(
        engine=get_engine(),
        vector_index=QdrantVectorIndex(
            url=settings.qdrant_url, collection_name=settings.qdrant_collection
        ),
    )


@router.get("", response_model=KnowledgeDocumentList)
def list_documents(
    context: Annotated[AgentContext, Depends(get_agent_context)],
    service: Annotated[RagDocumentManagementService, Depends(get_management_service)],
    keyword: Annotated[str | None, Query(max_length=100)] = None,
    document_type: Annotated[str | None, Query(alias="documentType")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> KnowledgeDocumentList:
    del context
    result = _invoke(
        lambda: service.list_documents(
            keyword=keyword, document_type=document_type, page=page, size=size
        )
    )
    return KnowledgeDocumentList(
        items=[KnowledgeDocumentItem.from_record(record) for record in result.items],
        total=result.total,
    )


@router.get("/{document_id}", response_model=KnowledgeDocumentDetail)
def get_document(
    document_id: Annotated[str, Path(min_length=5, max_length=64)],
    context: Annotated[AgentContext, Depends(get_agent_context)],
    service: Annotated[RagDocumentManagementService, Depends(get_management_service)],
) -> KnowledgeDocumentDetail:
    del context
    return KnowledgeDocumentDetail.from_record(_invoke(lambda: service.get_document(document_id)))


@router.post("", response_model=KnowledgeDocumentDetail, status_code=status.HTTP_201_CREATED)
def upload_document(
    body: KnowledgeDocumentUploadRequest,
    context: Annotated[AgentContext, Depends(get_agent_context)],
    service: Annotated[RagDocumentManagementService, Depends(get_management_service)],
) -> KnowledgeDocumentDetail:
    _require_admin(context)
    record = _invoke(
        lambda: service.upload(
            file_name=body.file_name,
            media_type=body.media_type,
            content_base64=body.content_base64,
            metadata=body.metadata,
        )
    )
    return KnowledgeDocumentDetail.from_record(record)


@router.put("/{document_id}", response_model=KnowledgeDocumentDetail)
def update_document(
    body: KnowledgeDocumentUpdateRequest,
    document_id: Annotated[str, Path(min_length=5, max_length=64)],
    context: Annotated[AgentContext, Depends(get_agent_context)],
    service: Annotated[RagDocumentManagementService, Depends(get_management_service)],
) -> KnowledgeDocumentDetail:
    _require_admin(context)
    record = _invoke(
        lambda: service.update_markdown(
            document_id=document_id,
            content=body.content,
            expected_record_version=body.expected_version,
        )
    )
    return KnowledgeDocumentDetail.from_record(record)


@router.delete("/{document_id}", response_model=KnowledgeDocumentDeleteResult)
def delete_document(
    document_id: Annotated[str, Path(min_length=5, max_length=64)],
    expected_version: Annotated[int, Query(alias="expectedVersion", ge=0)],
    context: Annotated[AgentContext, Depends(get_agent_context)],
    service: Annotated[RagDocumentManagementService, Depends(get_management_service)],
) -> KnowledgeDocumentDeleteResult:
    _require_admin(context)
    record = _invoke(
        lambda: service.delete(
            document_id=document_id, expected_record_version=expected_version
        )
    )
    return KnowledgeDocumentDeleteResult(
        document_id=record.document_id,
        status=record.status,
        record_version=record.record_version,
    )


def _require_admin(context: AgentContext) -> None:
    if not context.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="FORBIDDEN")


def _invoke(operation: Callable[[], ResultT]) -> ResultT:
    try:
        return operation()
    except RagDocumentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="RAG_DOCUMENT_NOT_FOUND"
        ) from exc
    except RagDocumentConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="RAG_DOCUMENT_CONFLICT"
        ) from exc
    except RagDocumentInvalidError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="RAG_DOCUMENT_INVALID"
        ) from exc
    except RagDocumentUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="AGENT_UNAVAILABLE"
        ) from exc
