"""Deterministic ingestion for meeting-policy Markdown and text PDFs."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from qdrant_client import QdrantClient, models
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.metadata import RagDocument
from app.rag.policies import VECTOR_SIZE, deterministic_embedding

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
    def replace_document(self, *, document_id: str, chunks: tuple[IngestedChunk, ...]) -> None: ...


@dataclass
class QdrantVectorIndex:
    url: str
    collection_name: str
    client: QdrantClient | None = None

    def _client(self) -> QdrantClient:
        if self.client is None:
            self.client = QdrantClient(url=self.url, timeout=10)
        return self.client

    def replace_document(self, *, document_id: str, chunks: tuple[IngestedChunk, ...]) -> None:
        client = self._client()
        try:
            if not client.collection_exists(self.collection_name):
                client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=models.VectorParams(
                        size=VECTOR_SIZE, distance=models.Distance.COSINE
                    ),
                )
            client.upsert(
                collection_name=self.collection_name,
                wait=True,
                points=[
                    models.PointStruct(
                        id=_point_id(chunk.chunk_id),
                        vector=deterministic_embedding(
                            " ".join((*chunk.heading_path, chunk.content))
                        ),
                        payload=chunk.payload(),
                    )
                    for chunk in chunks
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
        except Exception as exc:
            raise RagIngestionError("Qdrant document replacement failed") from exc


@dataclass
class RagDocumentRepository:
    engine: Engine

    def duplicate_for_checksum(self, checksum: str) -> RagDocument | None:
        with Session(self.engine) as session:
            return session.scalar(select(RagDocument).where(RagDocument.checksum == checksum))

    def begin(self, document: ParsedDocument) -> None:
        with Session(self.engine) as session:
            record = session.get(RagDocument, document.metadata.document_id)
            if record is None:
                record = RagDocument(
                    document_id=document.metadata.document_id,
                    title=document.metadata.title,
                    document_type=document.metadata.document_type,
                    source_path=document.source_path.as_posix(),
                    version=document.metadata.version,
                    checksum=document.checksum,
                    status="INDEXING",
                    chunk_count=0,
                    indexed_at=None,
                )
                session.add(record)
            else:
                record.title = document.metadata.title
                record.document_type = document.metadata.document_type
                record.source_path = document.source_path.as_posix()
                record.version = document.metadata.version
                record.checksum = document.checksum
                record.status = "INDEXING"
                record.chunk_count = 0
                record.indexed_at = None
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
            record.indexed_at = datetime.now(ZoneInfo("Asia/Shanghai"))
            session.commit()

    def mark_failed(self, document_id: str) -> None:
        with Session(self.engine) as session:
            record = session.get(RagDocument, document_id)
            if record is None:
                return
            record.status = "FAILED"
            record.chunk_count = 0
            record.indexed_at = None
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

    def ingest_file(self, source_path: Path) -> IngestionResult:
        document = parse_document(source_path)
        duplicate = self.repository.duplicate_for_checksum(document.checksum)
        if duplicate is not None and duplicate.status == "INDEXED":
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
        self.repository.begin(document)
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
        return ParsedDocument(metadata, path, checksum, tuple(pages))
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
        for index, (heading_path, page, content) in enumerate(sections, start=1)
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
