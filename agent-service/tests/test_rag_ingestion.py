from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from qdrant_client import QdrantClient, models
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.database.base import Base
from app.models.metadata import RagDocument
from app.rag.embeddings import DeterministicEmbeddingProvider
from app.rag.ingestion import (
    IngestedChunk,
    QdrantVectorIndex,
    RagDocumentRepository,
    RagIngestionError,
    RagIngestionService,
    chunk_document,
    parse_document,
)
from app.rag.policies import PolicyRetrievalError, QdrantPolicyRetriever

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
POLICY_SOURCE_DIR = REPOSITORY_ROOT / "deploy" / "rag-documents"


def _install_fake_pypdf(
    monkeypatch: pytest.MonkeyPatch, pages: list[SimpleNamespace]
) -> None:
    module = ModuleType("pypdf")
    module.PdfReader = lambda _: SimpleNamespace(pages=pages)  # type: ignore[attr-defined]
    monkeypatch.setitem(__import__("sys").modules, "pypdf", module)


class RecordingVectorIndex:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.documents: dict[str, tuple[IngestedChunk, ...]] = {}
        self.current_documents: dict[str, str] = {}
        self.calls: list[str] = []

    def document_is_current(self, *, document_id: str, checksum: str) -> bool:
        return self.current_documents.get(document_id) == checksum

    def replace_document(
        self, *, document_id: str, chunks: tuple[IngestedChunk, ...]
    ) -> None:
        self.calls.append(document_id)
        if self.fail:
            raise RagIngestionError("fixture vector failure")
        self.documents[document_id] = chunks
        if chunks:
            self.current_documents[document_id] = chunks[0].checksum

    def delete_document(self, *, document_id: str) -> None:
        self.calls.append(f"delete:{document_id}")
        if self.fail:
            raise RagIngestionError("fixture vector failure")
        self.documents.pop(document_id, None)
        self.current_documents.pop(document_id, None)


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


def _service(engine: Engine, index: RecordingVectorIndex) -> RagIngestionService:
    return RagIngestionService(RagDocumentRepository(engine), index)


def test_all_repository_policy_documents_parse_and_chunk() -> None:
    paths = sorted(POLICY_SOURCE_DIR.glob("*.md"))
    assert len(paths) == 22

    documents = [parse_document(path) for path in paths]
    assert len({document.metadata.document_id for document in documents}) == 22
    assert len({document.checksum for document in documents}) == 22
    for document in documents:
        chunks = chunk_document(document)
        assert chunks
        assert [chunk.chunk_id for chunk in chunks] == [
            f"chunk_{document.metadata.document_id}_{index:04d}"
            for index in range(1, len(chunks) + 1)
        ]
        assert all(chunk.heading_path for chunk in chunks)
        assert all(chunk.heading_path[0] == document.metadata.title for chunk in chunks)
        assert all(chunk.page == 1 for chunk in chunks)
        assert all(chunk.content for chunk in chunks)
        assert "RAG 测试问题" in document.content_text
        assert all("RAG 测试问题" not in chunk.heading_path for chunk in chunks)


def test_markdown_ingestion_registers_then_skips_same_checksum(rag_engine: Engine) -> None:
    index = RecordingVectorIndex()
    service = _service(rag_engine, index)
    source = POLICY_SOURCE_DIR / "07-vip-executive-room-policy.md"

    first = service.ingest_file(source)
    duplicate = service.ingest_file(source)

    assert first.status == "INDEXED"
    assert first.chunk_count > 0
    assert duplicate.status == "SKIPPED_DUPLICATE"
    assert duplicate.duplicate_of == "doc_vip_executive_room_policy"
    assert index.calls == ["doc_vip_executive_room_policy"]
    with Session(rag_engine) as session:
        records = list(session.scalars(select(RagDocument)))
    assert len(records) == 1
    assert records[0].status == "INDEXED"
    assert records[0].chunk_count == first.chunk_count
    assert records[0].indexed_at is not None


def test_same_checksum_is_reindexed_when_target_collection_is_new(
    rag_engine: Engine,
) -> None:
    index = RecordingVectorIndex()
    service = _service(rag_engine, index)
    source = POLICY_SOURCE_DIR / "07-vip-executive-room-policy.md"

    first = service.ingest_file(source)
    index.current_documents.clear()
    migrated = service.ingest_file(source)

    assert first.status == migrated.status == "INDEXED"
    assert index.calls == [
        "doc_vip_executive_room_policy",
        "doc_vip_executive_room_policy",
    ]
    with Session(rag_engine) as session:
        record = session.get(RagDocument, first.document_id)
    assert record is not None
    assert record.record_version == 0


def test_same_checksum_under_second_document_id_does_not_duplicate_index(
    tmp_path: Path, rag_engine: Engine
) -> None:
    source = POLICY_SOURCE_DIR / "10-architecture-review-standard.md"
    copied = tmp_path / "copied.md"
    copied.write_bytes(source.read_bytes())
    index = RecordingVectorIndex()
    service = _service(rag_engine, index)

    original = service.ingest_file(source)
    duplicate = service.ingest_file(copied)

    assert original.status == "INDEXED"
    assert duplicate.status == "SKIPPED_DUPLICATE"
    assert duplicate.duplicate_of == original.document_id
    assert len(index.calls) == 1


def test_changed_document_replaces_points_and_updates_checksum(
    tmp_path: Path, rag_engine: Engine
) -> None:
    source = POLICY_SOURCE_DIR / "20-whiteboard-collaboration-equipment-guide.md"
    changed = tmp_path / source.name
    changed.write_text(
        source.read_text(encoding="utf-8") + "\n新增可执行说明。\n", encoding="utf-8"
    )
    index = RecordingVectorIndex()
    service = _service(rag_engine, index)

    first = service.ingest_file(source)
    second = service.ingest_file(changed)

    assert first.status == second.status == "INDEXED"
    assert first.checksum != second.checksum
    assert index.calls == [
        "doc_whiteboard_collaboration_equipment_guide",
        "doc_whiteboard_collaboration_equipment_guide",
    ]
    with Session(rag_engine) as session:
        record = session.get(RagDocument, first.document_id)
    assert record is not None
    assert record.checksum == second.checksum
    assert record.source_path == changed.resolve().as_posix()


def test_index_failure_marks_document_failed(rag_engine: Engine) -> None:
    service = _service(rag_engine, RecordingVectorIndex(fail=True))
    source = POLICY_SOURCE_DIR / "01-meeting-room-management-policy.md"

    with pytest.raises(RagIngestionError, match="fixture vector failure"):
        service.ingest_file(source)

    with Session(rag_engine) as session:
        record = session.get(RagDocument, "doc_meeting_room_management_policy")
    assert record is not None
    assert record.status == "FAILED"
    assert record.chunk_count == 0
    assert record.indexed_at is None


def test_invalid_front_matter_is_rejected(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.md"
    invalid.write_text(
        """---
documentId: bad-id
title: 错误制度
documentType: UNKNOWN
department: ALL
version: "1.0"
effectiveDate: "2026-08-01"
status: DRAFT
priority: 100
timezone: UTC
---
# 错误制度
""",
        encoding="utf-8",
    )

    with pytest.raises(RagIngestionError, match="invalid metadata"):
        parse_document(invalid)


def test_text_pdf_uses_sidecar_metadata_and_preserves_page_numbers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = tmp_path / "policy.pdf"
    pdf.write_bytes(b"%PDF-fixture")
    pdf.with_suffix(".yaml").write_text(
        """documentId: doc_pdf_policy_fixture
title: PDF 会议制度
documentType: MEETING_POLICY
department: ALL
version: "1.0"
effectiveDate: "2026-08-01"
status: ACTIVE
priority: 100
timezone: Asia/Shanghai
""",
        encoding="utf-8",
    )
    fake_pages = [
        SimpleNamespace(extract_text=lambda: "# PDF 会议制度\n## 第一章\n必须执行第一条规则。"),
        SimpleNamespace(extract_text=lambda: "## 第二章\n不得绕过人工确认。"),
    ]
    _install_fake_pypdf(monkeypatch, fake_pages)

    document = parse_document(pdf)
    chunks = chunk_document(document)

    assert document.metadata.document_id == "doc_pdf_policy_fixture"
    assert {chunk.page for chunk in chunks} == {1, 2}
    assert any(chunk.heading_path[-1] == "第二章" for chunk in chunks)


def test_pdf_without_extractable_text_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-scan")
    _install_fake_pypdf(monkeypatch, [SimpleNamespace(extract_text=lambda: "")])

    with pytest.raises(RagIngestionError, match="OCR is not supported"):
        parse_document(pdf)


def test_qdrant_ingestion_retrieves_real_vip_chunk(rag_engine: Engine) -> None:
    client = QdrantClient(":memory:")
    collection = "meeting_policies_test"
    embeddings = DeterministicEmbeddingProvider()
    vector_index = QdrantVectorIndex(
        url="http://unused",
        collection_name=collection,
        embedding_provider=embeddings,
        client=client,
    )
    service = RagIngestionService(RagDocumentRepository(rag_engine), vector_index)
    service.ingest_file(POLICY_SOURCE_DIR / "07-vip-executive-room-policy.md")
    settings = Settings().model_copy(update={"qdrant_collection": collection})
    retriever = QdrantPolicyRetriever(
        settings=settings, embedding_provider=embeddings, client=client
    )

    candidates = retriever.search("VIP会议室普通内部会议可以使用吗", limit=5)

    assert candidates
    assert any(candidate.document_id == "doc_vip_executive_room_policy" for candidate in candidates)
    selected = next(
        candidate
        for candidate in candidates
        if candidate.document_id == "doc_vip_executive_room_policy"
    )
    assert selected.chunk_id.startswith("chunk_doc_vip_executive_room_policy_")
    assert retriever.open_candidates(
        candidates=candidates, selected_chunk_ids=[selected.chunk_id]
    ) == [selected]


def test_runtime_retriever_does_not_seed_a_missing_collection() -> None:
    client = QdrantClient(":memory:")
    collection = "missing_runtime_policy_corpus"
    settings = Settings().model_copy(update={"qdrant_collection": collection})
    retriever = QdrantPolicyRetriever(
        settings=settings,
        embedding_provider=DeterministicEmbeddingProvider(),
        client=client,
    )

    with pytest.raises(PolicyRetrievalError, match="corpus is unavailable"):
        retriever.search("会议制度", limit=5)

    assert not client.collection_exists(collection)


def test_qdrant_collection_dimension_mismatch_is_rejected() -> None:
    client = QdrantClient(":memory:")
    collection = "wrong_vector_dimension"
    client.create_collection(
        collection_name=collection,
        vectors_config=models.VectorParams(size=1024, distance=models.Distance.COSINE),
    )
    vector_index = QdrantVectorIndex(
        url="http://unused",
        collection_name=collection,
        embedding_provider=DeterministicEmbeddingProvider(),
        client=client,
    )

    with pytest.raises(RagIngestionError, match="vector dimension"):
        vector_index.document_is_current(document_id="doc_fixture", checksum="checksum")


def test_qdrant_lexical_boost_finds_vip_policy_in_full_corpus(rag_engine: Engine) -> None:
    client = QdrantClient(":memory:")
    collection = "meeting_policies_full_corpus_test"
    embeddings = DeterministicEmbeddingProvider()
    service = RagIngestionService(
        RagDocumentRepository(rag_engine),
        QdrantVectorIndex(
            url="http://unused",
            collection_name=collection,
            embedding_provider=embeddings,
            client=client,
        ),
    )
    service.ingest_directory(POLICY_SOURCE_DIR)
    settings = Settings().model_copy(update={"qdrant_collection": collection})
    retriever = QdrantPolicyRetriever(
        settings=settings, embedding_provider=embeddings, client=client
    )

    candidates = retriever.search("VIP会议室普通内部会议可以使用吗", limit=5)

    assert candidates
    assert candidates[0].document_id == "doc_vip_executive_room_policy"


def test_qdrant_replacement_removes_stale_chunks(tmp_path: Path, rag_engine: Engine) -> None:
    client = QdrantClient(":memory:")
    collection = "meeting_policies_replace_test"
    vector_index = QdrantVectorIndex(
        url="http://unused",
        collection_name=collection,
        embedding_provider=DeterministicEmbeddingProvider(),
        client=client,
    )
    service = RagIngestionService(RagDocumentRepository(rag_engine), vector_index)
    source = tmp_path / "replace.md"
    metadata = """---
documentId: doc_replace_fixture
title: 替换测试制度
documentType: MEETING_POLICY
department: ALL
version: "1.0"
effectiveDate: "2026-08-01"
status: ACTIVE
priority: 100
timezone: Asia/Shanghai
---
"""
    source.write_text(
        metadata + "# 替换测试制度\n## 第一章\n第一条规则。\n## 第二章\n第二条规则。\n",
        encoding="utf-8",
    )
    first = service.ingest_file(source)
    source.write_text(
        metadata + "# 替换测试制度\n## 第一章\n更新后的唯一规则。\n",
        encoding="utf-8",
    )
    second = service.ingest_file(source)
    points, _ = client.scroll(
        collection_name=collection,
        scroll_filter=models.Filter(
            must=[
                models.FieldCondition(
                    key="documentId", match=models.MatchValue(value="doc_replace_fixture")
                )
            ]
        ),
        limit=100,
        with_payload=True,
    )

    assert first.chunk_count == 2
    assert second.chunk_count == 1
    assert len(points) == 1
    assert points[0].payload is not None
    assert points[0].payload["chunkId"] == "chunk_doc_replace_fixture_0001"


def test_stable_chunk_ids_do_not_depend_on_source_path(tmp_path: Path) -> None:
    source = POLICY_SOURCE_DIR / "03-conflict-and-resource-allocation-policy.md"
    copied = tmp_path / "renamed.md"
    copied.write_bytes(source.read_bytes())

    original = parse_document(source)
    renamed = parse_document(copied)

    assert original.checksum == renamed.checksum
    assert chunk_document(original) == chunk_document(renamed)
