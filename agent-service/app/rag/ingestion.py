"""Meeting-policy Markdown/text-PDF ingestion backed by a shared vector model."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from qdrant_client import QdrantClient, models
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.models.metadata import RagDocument
from app.rag.embeddings import EmbeddingError, EmbeddingProvider, build_embedding_provider

ALLOWED_DOCUMENT_TYPES = {
    "MEETING_POLICY",
    "MEETING_STANDARD",
    "ROOM_POLICY",
    "SECURITY_POLICY",
    "EQUIPMENT_GUIDE",
    "DEPARTMENT_POLICY",
    "FAQ",
}
MAX_CHUNK_CHARACTERS = 1200
MIN_EXTRACTED_PDF_CHARACTERS = 20
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
DOCUMENT_ID_PATTERN = re.compile(r"^doc_[a-z0-9_]+$")


class RagIngestionError(RuntimeError):
    """Raised for a controlled, user-correctable ingestion failure."""


class DocumentMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    document_id: str = Field(alias="documentId", min_length=5, max_length=64)
    title: str = Field(min_length=1, max_length=255)
    document_type: str = Field(alias="documentType")
    department: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=32)
    effective_date: date = Field(alias="effectiveDate")
    status: str
    priority: int = Field(ge=0, le=1000)
    timezone: str

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        if not DOCUMENT_ID_PATTERN.fullmatch(value):
            raise ValueError("documentId must match doc_[a-z0-9_]+")
        return value

    @field_validator("document_type")
    @classmethod
    def validate_document_type(cls, value: str) -> str:
        if value not in ALLOWED_DOCUMENT_TYPES:
            raise ValueError("documentType is not allowed")
        return value

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        if value != "ACTIVE":
            raise ValueError("only ACTIVE documents can be indexed")
        return value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        if value != "Asia/Shanghai":
            raise ValueError("timezone must be Asia/Shanghai")
        return value


@dataclass(frozen=True)
class SourcePage:
    page: int
    text: str


@dataclass(frozen=True)
class ParsedDocument:
    metadata: DocumentMetadata
    source_path: Path
    checksum: str
    pages: tuple[SourcePage, ...]
    file_name: str
    media_type: str
    content_text: str


@dataclass(frozen=True)
class IngestedChunk:
    chunk_id: str
    document_id: str
    document_type: str
    title: str
    heading_path: tuple[str, ...]
    page: int
    content: str
    version: str
    priority: int
    checksum: str

    def payload(self) -> dict[str, object]:
        return {
            "chunkId": self.chunk_id,
            "documentId": self.document_id,
            "documentType": self.document_type,
            "title": self.title,
            "headingPath": list(self.heading_path),
            "page": self.page,
            "content": self.content,
            "version": self.version,
            "priority": self.priority,
            "checksum": self.checksum,
        }


@dataclass(frozen=True)
class IngestionResult:
    source_path: str
    document_id: str
    checksum: str
    status: str
    chunk_count: int
    duplicate_of: str | None = None


class VectorIndex(Protocol):
    def document_is_current(self, *, document_id: str, checksum: str) -> bool: ...

    def replace_document(self, *, document_id: str, chunks: tuple[IngestedChunk, ...]) -> None: ...

    def delete_document(self, *, document_id: str) -> None: ...


@dataclass
class QdrantVectorIndex:
    url: str
    collection_name: str
    embedding_provider: EmbeddingProvider
    client: QdrantClient | None = None

    def _client(self) -> QdrantClient:
        if self.client is None:
            self.client = QdrantClient(url=self.url, timeout=10)
        return self.client

    def document_is_current(self, *, document_id: str, checksum: str) -> bool:
        client = self._client()
        try:
            if not client.collection_exists(self.collection_name):
                return False
            self._ensure_collection(client, create=False)
            result = client.count(
                collection_name=self.collection_name,
                count_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="documentId", match=models.MatchValue(value=document_id)
                        ),
                        models.FieldCondition(
                            key="checksum", match=models.MatchValue(value=checksum)
                        ),
                        models.FieldCondition(
                            key="embeddingModel",
                            match=models.MatchValue(value=self.embedding_provider.model_id),
                        ),
                    ]
                ),
                exact=True,
            )
            return result.count > 0
        except RagIngestionError:
            raise
        except Exception as exc:
            raise RagIngestionError("Qdrant document state check failed") from exc

    def replace_document(self, *, document_id: str, chunks: tuple[IngestedChunk, ...]) -> None:
        client = self._client()
        try:
            self._ensure_collection(client, create=True)
            vectors = self.embedding_provider.embed_documents(
                [_embedding_text(chunk) for chunk in chunks]
            )
            client.upsert(
                collection_name=self.collection_name,
                wait=True,
                points=[
                    models.PointStruct(
                        id=_point_id(chunk.chunk_id),
                        vector=vector,
                        payload={
                            **chunk.payload(),
                            "embeddingModel": self.embedding_provider.model_id,
                            "vectorSize": self.embedding_provider.dimension,
                        },
                    )
                    for chunk, vector in zip(chunks, vectors, strict=True)
                ],
            )
            current_ids: list[int | str] = [_point_id(chunk.chunk_id) for chunk in chunks]
            client.delete(
                collection_name=self.collection_name,
                points_selector=models.FilterSelector(
                    filter=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="documentId", match=models.MatchValue(value=document_id)
                            )
                        ],
                        must_not=[models.HasIdCondition(has_id=current_ids)],
                    )
                ),
                wait=True,
            )
        except EmbeddingError as exc:
            raise RagIngestionError("embedding model is unavailable") from exc
        except RagIngestionError:
            raise
        except Exception as exc:
            raise RagIngestionError("Qdrant document replacement failed") from exc

    def delete_document(self, *, document_id: str) -> None:
        client = self._client()
        try:
            if not client.collection_exists(self.collection_name):
                return
            client.delete(
                collection_name=self.collection_name,
                points_selector=models.FilterSelector(
                    filter=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="documentId", match=models.MatchValue(value=document_id)
                            )
                        ]
                    )
                ),
                wait=True,
            )
        except Exception as exc:
            raise RagIngestionError("Qdrant document deletion failed") from exc

    def _ensure_collection(self, client: QdrantClient, *, create: bool) -> None:
        if not client.collection_exists(self.collection_name):
            if not create:
                raise RagIngestionError("Qdrant collection is unavailable")
            client.create_collection(
                collection_name=self.collection_name,
                vectors_config=models.VectorParams(
                    size=self.embedding_provider.dimension,
                    distance=models.Distance.COSINE,
                ),
            )
            return
        info = client.get_collection(self.collection_name)
        vectors_config: Any = info.config.params.vectors
        actual_size = getattr(vectors_config, "size", None)
        if actual_size != self.embedding_provider.dimension:
            raise RagIngestionError(
                "Qdrant collection vector dimension does not match the embedding model"
            )


def build_vector_index(
    settings: Settings, *, client: QdrantClient | None = None
) -> QdrantVectorIndex:
    return QdrantVectorIndex(
        url=settings.qdrant_url,
        collection_name=settings.qdrant_collection,
        embedding_provider=build_embedding_provider(settings),
        client=client,
    )


@dataclass
class RagDocumentRepository:
    engine: Engine

    def duplicate_for_checksum(self, checksum: str) -> RagDocument | None:
        with Session(self.engine) as session:
            return session.scalar(select(RagDocument).where(RagDocument.checksum == checksum))

    def find(self, document_id: str) -> RagDocument | None:
        with Session(self.engine) as session:
            return session.get(RagDocument, document_id)

    def begin(
        self,
        document: ParsedDocument,
        *,
        expected_record_version: int | None = None,
        allow_restore: bool = False,
    ) -> None:
        with Session(self.engine) as session:
            record = session.scalar(
                select(RagDocument)
                .where(RagDocument.document_id == document.metadata.document_id)
                .with_for_update()
            )
            if record is None:
                if expected_record_version is not None:
                    raise RagIngestionError("document management version is stale")
                record = RagDocument(
                    document_id=document.metadata.document_id,
                    title=document.metadata.title,
                    document_type=document.metadata.document_type,
                    department=document.metadata.department,
                    effective_date=document.metadata.effective_date,
                    priority=document.metadata.priority,
                    source_path=document.source_path.as_posix(),
                    file_name=document.file_name,
                    media_type=document.media_type,
                    content_text=document.content_text,
                    version=document.metadata.version,
                    checksum=document.checksum,
                    status="INDEXING",
                    chunk_count=0,
                    record_version=0,
                    indexed_at=None,
                    deleted_at=None,
                )
                session.add(record)
            else:
                if (
                    expected_record_version is not None
                    and record.record_version != expected_record_version
                ):
                    raise RagIngestionError("document management version is stale")
                if record.status == "DELETED" and not allow_restore:
                    raise RagIngestionError("document is deleted")
                record.title = document.metadata.title
                record.document_type = document.metadata.document_type
                record.department = document.metadata.department
                record.effective_date = document.metadata.effective_date
                record.priority = document.metadata.priority
                record.source_path = document.source_path.as_posix()
                record.file_name = document.file_name
                record.media_type = document.media_type
                record.content_text = document.content_text
                record.version = document.metadata.version
                record.checksum = document.checksum
                record.status = "INDEXING"
                record.chunk_count = 0
                record.record_version += 1
                record.indexed_at = None
                record.deleted_at = None
            record.updated_at = datetime.now(ZoneInfo("Asia/Shanghai"))
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise RagIngestionError("document checksum is already registered") from exc

    def mark_indexed(self, document_id: str, chunk_count: int) -> None:
        with Session(self.engine) as session:
            record = session.get(RagDocument, document_id)
            if record is None:
                raise RagIngestionError("rag_document registration disappeared")
            record.status = "INDEXED"
            record.chunk_count = chunk_count
            now = datetime.now(ZoneInfo("Asia/Shanghai"))
            record.indexed_at = now
            record.updated_at = now
            session.commit()

    def mark_failed(self, document_id: str) -> None:
        with Session(self.engine) as session:
            record = session.get(RagDocument, document_id)
            if record is None:
                return
            record.status = "FAILED"
            record.chunk_count = 0
            record.indexed_at = None
            record.updated_at = datetime.now(ZoneInfo("Asia/Shanghai"))
            session.commit()


@dataclass
class RagIngestionService:
    repository: RagDocumentRepository
    vector_index: VectorIndex

    def ingest_directory(self, source_dir: Path) -> list[IngestionResult]:
        resolved = source_dir.resolve()
        if not resolved.is_dir():
            raise RagIngestionError(f"RAG source directory does not exist: {resolved}")
        sources = sorted(
            path
            for path in resolved.iterdir()
            if path.is_file() and path.suffix.lower() in {".md", ".pdf"}
        )
        if not sources:
            raise RagIngestionError(
                f"RAG source directory contains no Markdown or PDF files: {resolved}"
            )
        results: list[IngestionResult] = []
        failures: list[str] = []
        for source in sources:
            try:
                results.append(self.ingest_file(source))
            except RagIngestionError as exc:
                failures.append(f"{source.name}: {exc}")
        if failures:
            raise RagIngestionError("; ".join(failures))
        return results

    def ingest_file(
        self,
        source_path: Path,
        *,
        expected_record_version: int | None = None,
        allow_restore: bool = False,
        managed_source_path: Path | None = None,
    ) -> IngestionResult:
        document = parse_document(source_path)
        if managed_source_path is not None:
            document = replace(document, source_path=managed_source_path)
        existing = self.repository.find(document.metadata.document_id)
        if existing is not None and existing.status == "DELETED" and not allow_restore:
            return IngestionResult(
                source_path=document.source_path.as_posix(),
                document_id=document.metadata.document_id,
                checksum=document.checksum,
                status="SKIPPED_DELETED",
                chunk_count=0,
                duplicate_of=document.metadata.document_id,
            )
        duplicate = self.repository.duplicate_for_checksum(document.checksum)
        indexed_duplicate = (
            duplicate is not None
            and duplicate.status == "INDEXED"
            and bool(duplicate.content_text)
        )
        reindex_existing = bool(
            indexed_duplicate
            and duplicate is not None
            and duplicate.document_id == document.metadata.document_id
            and not self.vector_index.document_is_current(
                document_id=document.metadata.document_id,
                checksum=document.checksum,
            )
        )
        if indexed_duplicate and not reindex_existing:
            assert duplicate is not None
            return IngestionResult(
                source_path=document.source_path.as_posix(),
                document_id=document.metadata.document_id,
                checksum=document.checksum,
                status="SKIPPED_DUPLICATE",
                chunk_count=duplicate.chunk_count,
                duplicate_of=duplicate.document_id,
            )
        chunks = chunk_document(document)
        if not chunks:
            raise RagIngestionError("document produced no indexable chunks")
        if reindex_existing:
            try:
                self.vector_index.replace_document(
                    document_id=document.metadata.document_id, chunks=chunks
                )
                self.repository.mark_indexed(document.metadata.document_id, len(chunks))
            except Exception as exc:
                self.repository.mark_failed(document.metadata.document_id)
                if isinstance(exc, RagIngestionError):
                    raise
                raise RagIngestionError("document indexing failed") from exc
            return IngestionResult(
                source_path=document.source_path.as_posix(),
                document_id=document.metadata.document_id,
                checksum=document.checksum,
                status="INDEXED",
                chunk_count=len(chunks),
            )
        self.repository.begin(
            document,
            expected_record_version=expected_record_version,
            allow_restore=allow_restore,
        )
        try:
            self.vector_index.replace_document(
                document_id=document.metadata.document_id, chunks=chunks
            )
            self.repository.mark_indexed(document.metadata.document_id, len(chunks))
        except Exception as exc:
            self.repository.mark_failed(document.metadata.document_id)
            if isinstance(exc, RagIngestionError):
                raise
            raise RagIngestionError("document indexing failed") from exc
        return IngestionResult(
            source_path=document.source_path.as_posix(),
            document_id=document.metadata.document_id,
            checksum=document.checksum,
            status="INDEXED",
            chunk_count=len(chunks),
        )


def parse_document(source_path: Path) -> ParsedDocument:
    path = source_path.resolve()
    suffix = path.suffix.lower()
    if suffix == ".md":
        raw = path.read_bytes()
        normalized = _normalize_text(_decode_utf8(raw, path))
        metadata_text, body = _split_front_matter(normalized, path)
        metadata = _validate_metadata(_parse_scalar_mapping(metadata_text, path), path)
        return ParsedDocument(
            metadata=metadata,
            source_path=path,
            checksum=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
            pages=(SourcePage(page=1, text=body),),
            file_name=path.name,
            media_type="text/markdown",
            content_text=normalized,
        )
    if suffix == ".pdf":
        raw = path.read_bytes()
        pages = _extract_pdf_pages(path)
        sidecar = _find_pdf_sidecar(path)
        if sidecar is not None:
            metadata_text = _normalize_text(_decode_utf8(sidecar.read_bytes(), sidecar))
        else:
            metadata_text, first_page = _split_front_matter(pages[0].text, path)
            pages = (replace(pages[0], text=first_page), *pages[1:])
        metadata = _validate_metadata(_parse_scalar_mapping(metadata_text, path), path)
        normalized_metadata = "\n".join(
            f"{key}: {value}"
            for key, value in sorted(metadata.model_dump(by_alias=True, mode="json").items())
        )
        checksum = hashlib.sha256(raw + b"\0" + normalized_metadata.encode("utf-8")).hexdigest()
        return ParsedDocument(
            metadata=metadata,
            source_path=path,
            checksum=checksum,
            pages=tuple(pages),
            file_name=path.name,
            media_type="application/pdf",
            content_text="\n\n".join(page.text for page in pages).strip(),
        )
    raise RagIngestionError(f"unsupported document type: {path.suffix}")


def chunk_document(document: ParsedDocument) -> tuple[IngestedChunk, ...]:
    sections: list[tuple[tuple[str, ...], int, str]] = []
    heading_stack: list[str] = [document.metadata.title]
    for page in document.pages:
        buffer: list[str] = []

        for line in page.text.splitlines():
            match = HEADING_PATTERN.match(line.strip())
            if match:
                _flush_section(sections, heading_stack, page.page, buffer)
                level = len(match.group(1))
                heading = match.group(2).strip()
                if level == 1:
                    heading_stack = [heading]
                else:
                    heading_stack = heading_stack[: level - 1]
                    while len(heading_stack) < level - 1:
                        heading_stack.append("未命名章节")
                    heading_stack.append(heading)
            else:
                buffer.append(line)
        _flush_section(sections, heading_stack, page.page, buffer)
    indexable_sections = [
        section for section in sections if not _is_rag_test_heading(section[0])
    ]
    return tuple(
        IngestedChunk(
            chunk_id=f"chunk_{document.metadata.document_id}_{index:04d}",
            document_id=document.metadata.document_id,
            document_type=document.metadata.document_type,
            title=document.metadata.title,
            heading_path=heading_path,
            page=page,
            content=content,
            version=document.metadata.version,
            priority=document.metadata.priority,
            checksum=document.checksum,
        )
        for index, (heading_path, page, content) in enumerate(indexable_sections, start=1)
    )


def _is_rag_test_heading(heading_path: tuple[str, ...]) -> bool:
    return any(
        re.sub(r"\s+", " ", heading.strip().lower()) == "rag 测试问题"
        for heading in heading_path
    )


def _flush_section(
    sections: list[tuple[tuple[str, ...], int, str]],
    heading_stack: list[str],
    page: int,
    buffer: list[str],
) -> None:
    content = _normalize_content("\n".join(buffer))
    buffer.clear()
    if content:
        sections.extend(
            (tuple(heading_stack), page, part) for part in _split_long_content(content)
        )


def _split_long_content(content: str) -> list[str]:
    if len(content) <= MAX_CHUNK_CHARACTERS:
        return [content]
    paragraphs = re.split(r"\n\s*\n", content)
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        candidate = f"{current}\n\n{paragraph}".strip()
        if current and len(candidate) > MAX_CHUNK_CHARACTERS:
            chunks.append(current)
            current = paragraph
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _extract_pdf_pages(path: Path) -> tuple[SourcePage, ...]:
    try:
        from pypdf import PdfReader

        reader = PdfReader(path)
        pages = tuple(
            SourcePage(page=index, text=_normalize_text(page.extract_text() or ""))
            for index, page in enumerate(reader.pages, start=1)
        )
    except Exception as exc:
        raise RagIngestionError(f"cannot read text PDF: {path.name}") from exc
    extracted = "".join(page.text for page in pages).strip()
    if len(extracted) < MIN_EXTRACTED_PDF_CHARACTERS:
        raise RagIngestionError("PDF contains no extractable text; OCR is not supported")
    return pages


def _find_pdf_sidecar(path: Path) -> Path | None:
    for suffix in (".yaml", ".yml"):
        candidate = path.with_suffix(suffix)
        if candidate.is_file():
            return candidate
    return None


def _split_front_matter(text: str, path: Path) -> tuple[str, str]:
    match = re.match(r"\A---\n(.*?)\n---(?:\n|\Z)(.*)\Z", text, re.DOTALL)
    if match is None:
        raise RagIngestionError(f"missing YAML Front Matter: {path.name}")
    return match.group(1), match.group(2).lstrip("\n")


def _parse_scalar_mapping(text: str, path: Path) -> dict[str, object]:
    values: dict[str, object] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise RagIngestionError(f"invalid metadata line {line_number} in {path.name}")
        key, raw_value = line.split(":", 1)
        key = key.strip()
        if not key or key in values:
            raise RagIngestionError(f"duplicate or empty metadata key in {path.name}")
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = int(value) if re.fullmatch(r"-?\d+", value) else value
    return values


def _validate_metadata(values: dict[str, object], path: Path) -> DocumentMetadata:
    try:
        return DocumentMetadata.model_validate(values)
    except ValidationError as exc:
        errors = ", ".join(
            f"{'.'.join(str(item) for item in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        raise RagIngestionError(f"invalid metadata in {path.name}: {errors}") from exc


def _decode_utf8(raw: bytes, path: Path) -> str:
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise RagIngestionError(f"document is not UTF-8: {path.name}") from exc


def _normalize_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _normalize_content(text: str) -> str:
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(lines).strip()


def _point_id(chunk_id: str) -> int:
    return int.from_bytes(hashlib.sha256(chunk_id.encode("utf-8")).digest()[:8], "big") >> 1


def _embedding_text(chunk: IngestedChunk) -> str:
    return " ".join((chunk.title, *chunk.heading_path, chunk.content))
