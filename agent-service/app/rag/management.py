"""Read and explicitly manage the meeting-policy knowledge corpus."""

from __future__ import annotations

import base64
import binascii
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from threading import Lock
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.models.metadata import RagDocument
from app.rag.ingestion import (
    ALLOWED_DOCUMENT_TYPES,
    DocumentMetadata,
    QdrantVectorIndex,
    RagDocumentRepository,
    RagIngestionError,
    RagIngestionService,
    parse_document,
)

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_MARKDOWN_CHARACTERS = 500_000


class RagDocumentNotFoundError(LookupError):
    """Raised when a visible managed document does not exist."""


class RagDocumentConflictError(RuntimeError):
    """Raised for checksum, state or optimistic-version conflicts."""


class RagDocumentInvalidError(ValueError):
    """Raised when a managed source cannot be safely parsed or indexed."""


class RagDocumentUnavailableError(RuntimeError):
    """Raised when the vector index cannot complete a management mutation."""


@dataclass(frozen=True)
class RagDocumentPage:
    items: list[RagDocument]
    total: int


@dataclass
class RagDocumentManagementService:
    engine: Engine
    vector_index: QdrantVectorIndex
    _mutation_lock: Lock = field(default_factory=Lock)

    def list_documents(
        self,
        *,
        keyword: str | None,
        document_type: str | None,
        page: int,
        size: int,
    ) -> RagDocumentPage:
        if document_type is not None and document_type not in ALLOWED_DOCUMENT_TYPES:
            raise RagDocumentInvalidError("documentType is not allowed")
        filters = [RagDocument.status != "DELETED"]
        normalized_keyword = (keyword or "").strip()
        if normalized_keyword:
            pattern = f"%{normalized_keyword[:100]}%"
            filters.append(
                or_(
                    RagDocument.title.like(pattern),
                    RagDocument.department.like(pattern),
                    RagDocument.document_id.like(pattern),
                )
            )
        if document_type:
            filters.append(RagDocument.document_type == document_type)
        with Session(self.engine) as session:
            total = session.scalar(select(func.count()).select_from(RagDocument).where(*filters))
            items = list(
                session.scalars(
                    select(RagDocument)
                    .where(*filters)
                    .order_by(RagDocument.priority.desc(), RagDocument.title.asc())
                    .offset((page - 1) * size)
                    .limit(size)
                )
            )
        return RagDocumentPage(items=items, total=int(total or 0))

    def get_document(self, document_id: str) -> RagDocument:
        with Session(self.engine) as session:
            record = session.get(RagDocument, document_id)
            if record is None or record.status == "DELETED":
                raise RagDocumentNotFoundError(document_id)
            return record

    def upload(
        self,
        *,
        file_name: str,
        media_type: str,
        content_base64: str,
        metadata: DocumentMetadata | None,
    ) -> RagDocument:
        safe_name, suffix = _validate_file(file_name=file_name, media_type=media_type)
        raw = _decode_upload(content_base64)
        with self._mutation_lock, tempfile.TemporaryDirectory(prefix="meetops-rag-") as temp_dir:
            source = Path(temp_dir) / safe_name
            source.write_bytes(raw)
            if suffix == ".pdf":
                if metadata is None:
                    raise RagDocumentInvalidError("PDF metadata is required")
                source.with_suffix(".yaml").write_text(
                    _metadata_sidecar(metadata), encoding="utf-8", newline="\n"
                )
            try:
                parsed = parse_document(source)
                if len(parsed.content_text) > MAX_MARKDOWN_CHARACTERS:
                    raise RagDocumentInvalidError("document content length is invalid")
                existing = RagDocumentRepository(self.engine).find(parsed.metadata.document_id)
                if existing is not None and existing.status != "DELETED":
                    raise RagDocumentConflictError("documentId is already registered")
                result = self._ingestion_service().ingest_file(
                    source,
                    allow_restore=existing is not None,
                    managed_source_path=Path("/managed") / safe_name,
                )
                if result.status == "SKIPPED_DUPLICATE":
                    raise RagDocumentConflictError("document checksum is already registered")
            except RagDocumentConflictError:
                raise
            except RagIngestionError as exc:
                raise _classify_ingestion_error(exc) from exc
        return self.get_document(parsed.metadata.document_id)

    def update_markdown(
        self, *, document_id: str, content: str, expected_record_version: int
    ) -> RagDocument:
        if not content.strip() or len(content) > MAX_MARKDOWN_CHARACTERS:
            raise RagDocumentInvalidError("Markdown content length is invalid")
        with self._mutation_lock:
            current = self.get_document(document_id)
            if current.record_version != expected_record_version:
                raise RagDocumentConflictError("document management version is stale")
            if current.media_type != "text/markdown":
                raise RagDocumentInvalidError("PDF documents must be replaced by upload")
            with tempfile.TemporaryDirectory(prefix="meetops-rag-") as temp_dir:
                safe_name = (
                    current.file_name
                    if current.file_name.endswith(".md")
                    else f"{document_id}.md"
                )
                source = Path(temp_dir) / safe_name
                source.write_text(content, encoding="utf-8", newline="\n")
                try:
                    parsed = parse_document(source)
                    if parsed.metadata.document_id != document_id:
                        raise RagDocumentInvalidError("documentId cannot be changed")
                    if parsed.checksum == current.checksum:
                        return current
                    result = self._ingestion_service().ingest_file(
                        source,
                        expected_record_version=expected_record_version,
                        managed_source_path=Path("/managed") / safe_name,
                    )
                    if result.status == "SKIPPED_DUPLICATE":
                        raise RagDocumentConflictError("document checksum is already registered")
                except (RagDocumentConflictError, RagDocumentInvalidError):
                    raise
                except RagIngestionError as exc:
                    raise _classify_ingestion_error(exc) from exc
        return self.get_document(document_id)

    def delete(self, *, document_id: str, expected_record_version: int) -> RagDocument:
        with self._mutation_lock:
            current = self.get_document(document_id)
            if current.record_version != expected_record_version:
                raise RagDocumentConflictError("document management version is stale")
            try:
                self.vector_index.delete_document(document_id=document_id)
            except RagIngestionError as exc:
                raise RagDocumentUnavailableError("document index deletion failed") from exc
            now = datetime.now(ZoneInfo("Asia/Shanghai"))
            with Session(self.engine, expire_on_commit=False) as session:
                record = session.scalar(
                    select(RagDocument)
                    .where(RagDocument.document_id == document_id)
                    .with_for_update()
                )
                if record is None or record.status == "DELETED":
                    raise RagDocumentNotFoundError(document_id)
                if record.record_version != expected_record_version:
                    raise RagDocumentConflictError("document management version is stale")
                record.status = "DELETED"
                record.chunk_count = 0
                record.content_text = ""
                record.record_version += 1
                record.indexed_at = None
                record.updated_at = now
                record.deleted_at = now
                session.commit()
                return record

    def _ingestion_service(self) -> RagIngestionService:
        return RagIngestionService(RagDocumentRepository(self.engine), self.vector_index)


def _validate_file(*, file_name: str, media_type: str) -> tuple[str, str]:
    safe_name = Path(file_name).name
    if not safe_name or safe_name != file_name or len(safe_name) > 255:
        raise RagDocumentInvalidError("fileName is invalid")
    suffix = Path(safe_name).suffix.lower()
    expected_media_type = {".md": "text/markdown", ".pdf": "application/pdf"}.get(suffix)
    if expected_media_type is None or media_type != expected_media_type:
        raise RagDocumentInvalidError("only Markdown and text PDF are supported")
    return safe_name, suffix


def _decode_upload(value: str) -> bytes:
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RagDocumentInvalidError("file content is not valid Base64") from exc
    if not raw or len(raw) > MAX_UPLOAD_BYTES:
        raise RagDocumentInvalidError("file size is invalid")
    return raw


def _metadata_sidecar(metadata: DocumentMetadata) -> str:
    values = metadata.model_dump(by_alias=True, mode="json")
    if any("\n" in str(value) or "\r" in str(value) for value in values.values()):
        raise RagDocumentInvalidError("metadata cannot contain line breaks")
    return "\n".join(f"{key}: {value}" for key, value in values.items()) + "\n"


def _classify_ingestion_error(error: RagIngestionError) -> Exception:
    message = str(error)
    if "version is stale" in message or "checksum is already" in message:
        return RagDocumentConflictError(message)
    if "Qdrant" in message or "indexing failed" in message:
        return RagDocumentUnavailableError(message)
    return RagDocumentInvalidError(message)
