"""Qdrant-backed meeting-policy retrieval with verifiable evidence payloads."""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from typing import Any, Protocol

from qdrant_client import QdrantClient

from app.config import Settings
from app.rag.embeddings import EmbeddingError, EmbeddingProvider, build_embedding_provider
from app.schemas.agent import Citation

MAX_SEARCH_CANDIDATES = 200
NON_SUBSTANTIVE_POLICY_HEADINGS = {"rag 找不到依据"}
logger = logging.getLogger(__name__)
_EMBEDDING_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rag-embedding")


class PolicyRetrievalError(RuntimeError):
    pass


@dataclass(frozen=True)
class PolicyChunk:
    chunk_id: str
    title: str
    heading_path: tuple[str, ...]
    page: int
    content: str
    score: float = 0.0
    document_id: str | None = None
    document_type: str | None = None
    version: str | None = None
    priority: int | None = None
    checksum: str | None = None

    def citation(self) -> Citation:
        return Citation(
            chunk_id=self.chunk_id,
            title=self.title,
            heading_path=list(self.heading_path),
            page=self.page,
        )


class PolicyRetriever(Protocol):
    def search(self, query: str, limit: int = 5) -> list[PolicyChunk]: ...

    def open_candidates(
        self, *, candidates: list[PolicyChunk], selected_chunk_ids: list[str]
    ) -> list[PolicyChunk]: ...


SEED_CHUNKS: tuple[PolicyChunk, ...] = (
    PolicyChunk(
        chunk_id="chunk_architecture_review_v1",
        title="架构评审规范",
        heading_path=("架构评审", "会议室与设备"),
        page=2,
        content="架构评审展示材料时应选择配备大屏的会议室；会议时长建议不超过120分钟。",
    ),
    PolicyChunk(
        chunk_id="chunk_vip_room_v1",
        title="VIP会议室使用规则",
        heading_path=("VIP会议室", "预约规则"),
        page=1,
        content="VIP会议室仅用于重要客户或公司级会议，使用前应遵循管理员审批要求。",
    ),
    PolicyChunk(
        chunk_id="chunk_room_equipment_v1",
        title="会议室设备说明",
        heading_path=("设备", "大屏与白板"),
        page=1,
        content=(
            "需要演示材料时应选择具备LARGE_SCREEN设备的会议室；白板需求应作为房间硬约束；"
            "需要远程参会时应选择具备VIDEO_CONFERENCE设备的会议室。"
        ),
    ),
    PolicyChunk(
        chunk_id="chunk_meeting_mutation_v1",
        title="会议改期与取消规则",
        heading_path=("会议变更", "人工确认"),
        page=3,
        content=(
            "Agent 发起的会议改期或取消必须先展示目标会议或变更草案，经用户确认后才可执行；"
            "拒绝草案不得改变正式会议。"
        ),
    ),
)


def _lexical_terms(text: str) -> set[str]:
    normalized = text.lower()
    terms = set(re.findall(r"[a-z0-9_]+", normalized))
    for sequence in re.findall(r"[\u4e00-\u9fff]+", normalized):
        for width in (1, 2, 3):
            terms.update(
                sequence[index : index + width]
                for index in range(len(sequence) - width + 1)
            )
    return terms


def _lexical_score(query: str, chunk: PolicyChunk) -> float:
    query_terms = _lexical_terms(query)
    if not query_terms:
        return 0.0
    heading_terms = _lexical_terms(" ".join((chunk.title, *chunk.heading_path)))
    content_terms = _lexical_terms(chunk.content)

    def term_weight(term: str) -> float:
        return float(min(len(term), 3) ** 2)

    heading_score = sum(term_weight(term) for term in query_terms & heading_terms)
    content_score = sum(term_weight(term) for term in query_terms & content_terms)
    return heading_score * 3.0 + content_score


@dataclass
class InMemoryPolicyRetriever:
    """Injectable deterministic retriever for unit tests only."""

    chunks: tuple[PolicyChunk, ...] = SEED_CHUNKS

    def search(self, query: str, limit: int = 5) -> list[PolicyChunk]:
        query_terms = set(re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]{1,2}", query.lower()))
        ranked = []
        for chunk in self.chunks:
            chunk_terms = set(
                re.findall(
                    r"[a-z0-9_]+|[\u4e00-\u9fff]{1,2}",
                    f"{chunk.title} {chunk.content}".lower(),
                )
            )
            score = float(len(query_terms.intersection(chunk_terms)))
            ranked.append(
                PolicyChunk(
                    chunk_id=chunk.chunk_id,
                    title=chunk.title,
                    heading_path=chunk.heading_path,
                    page=chunk.page,
                    content=chunk.content,
                    score=score,
                )
            )
        return sorted(ranked, key=lambda item: (-item.score, item.chunk_id))[:limit]

    def open_candidates(
        self, *, candidates: list[PolicyChunk], selected_chunk_ids: list[str]
    ) -> list[PolicyChunk]:
        return _open_candidates(candidates, selected_chunk_ids)


@dataclass
class QdrantPolicyRetriever:
    settings: Settings
    embedding_provider: EmbeddingProvider | None = None
    client: QdrantClient | None = None
    _validated_dimension: int | None = None
    _validated_at: float = 0.0

    def _qdrant(self) -> QdrantClient:
        if self.client is None:
            self.client = QdrantClient(url=self.settings.qdrant_url, timeout=5)
        return self.client

    def search(self, query: str, limit: int = 5) -> list[PolicyChunk]:
        candidate_limit = min(MAX_SEARCH_CANDIDATES, max(limit, limit * 20))
        started = time.perf_counter()
        embedding_started = started
        fallback = False
        provider: EmbeddingProvider | None = None
        future: Future[list[float]] | None = None
        try:
            provider = self._embedding()
            self._validate_collection(provider.dimension)
            future = _EMBEDDING_EXECUTOR.submit(provider.embed_query, query)
            vector = future.result(timeout=self.settings.rag_embedding_timeout_seconds)
            embedding_ms = int((time.perf_counter() - embedding_started) * 1000)
            search_started = time.perf_counter()
            result = self._qdrant().query_points(
                collection_name=self.settings.qdrant_collection,
                query=vector,
                limit=candidate_limit,
                with_payload=True,
            )
            vector_search_ms = int((time.perf_counter() - search_started) * 1000)
            chunks = self._chunks_from_points(list(result.points))
        except (EmbeddingError, FutureTimeoutError) as exc:
            if future is not None:
                future.cancel()
            fallback = True
            embedding_ms = int((time.perf_counter() - embedding_started) * 1000)
            search_started = time.perf_counter()
            try:
                chunks = self._lexical_fallback(query)
            except Exception as fallback_exc:
                raise PolicyRetrievalError(
                    "policy embedding and fallback search failed"
                ) from fallback_exc
            vector_search_ms = int((time.perf_counter() - search_started) * 1000)
            logger.warning(
                "Policy embedding exceeded budget; using bounded lexical fallback "
                "queryHash=%s reason=%s",
                _query_hash(query),
                type(exc).__name__,
            )
        except PolicyRetrievalError:
            raise
        except Exception as exc:  # Qdrant client has several transport exception classes.
            raise PolicyRetrievalError("Qdrant policy search failed") from exc
        ranked = sorted(
            chunks,
            key=lambda chunk: (
                -(_lexical_score(query, chunk) + chunk.score),
                chunk.chunk_id,
            ),
        )[:limit]
        cache_hit_reader = getattr(provider, "last_query_cache_hit", None)
        cache_hit = (
            bool(cache_hit_reader())
            if callable(cache_hit_reader) and not fallback
            else False
        )
        logger.info(
            "Policy retrieval completed queryHash=%s embeddingMs=%d vectorSearchMs=%d "
            "totalMs=%d cacheHit=%s fallback=%s resultCount=%d",
            _query_hash(query),
            embedding_ms,
            vector_search_ms,
            int((time.perf_counter() - started) * 1000),
            cache_hit,
            fallback,
            len(ranked),
        )
        return ranked

    def open_candidates(
        self, *, candidates: list[PolicyChunk], selected_chunk_ids: list[str]
    ) -> list[PolicyChunk]:
        return _open_candidates(candidates, selected_chunk_ids)

    def _embedding(self) -> EmbeddingProvider:
        if self.embedding_provider is None:
            self.embedding_provider = build_embedding_provider(self.settings)
        return self.embedding_provider

    def _validate_collection(self, expected_dimension: int) -> None:
        now = time.monotonic()
        if self._validated_dimension == expected_dimension and now - self._validated_at < 300:
            return
        client = self._qdrant()
        if not client.collection_exists(self.settings.qdrant_collection):
            raise PolicyRetrievalError("Qdrant policy corpus is unavailable")
        info = client.get_collection(self.settings.qdrant_collection)
        vectors_config: Any = info.config.params.vectors
        if getattr(vectors_config, "size", None) != expected_dimension:
            raise PolicyRetrievalError(
                "Qdrant policy collection does not match the embedding model"
            )
        self._validated_dimension = expected_dimension
        self._validated_at = now

    @staticmethod
    def _chunks_from_points(points: list[Any]) -> list[PolicyChunk]:
        chunks: list[PolicyChunk] = []
        for point in points:
            payload = point.payload
            if not isinstance(payload, dict):
                continue
            score = float(point.score) if getattr(point, "score", None) is not None else 0.0
            chunk = _chunk_from_payload(payload, score)
            if chunk is not None and not _is_non_substantive_policy_chunk(chunk):
                chunks.append(chunk)
        return chunks

    def _lexical_fallback(self, query: str) -> list[PolicyChunk]:
        points: list[Any] = []
        offset: Any = None
        while len(points) < MAX_SEARCH_CANDIDATES:
            batch, offset = self._qdrant().scroll(
                collection_name=self.settings.qdrant_collection,
                limit=min(100, MAX_SEARCH_CANDIDATES - len(points)),
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            points.extend(batch)
            if offset is None or not batch:
                break
        chunks = self._chunks_from_points(points)
        return sorted(
            chunks,
            key=lambda chunk: (-_lexical_score(query, chunk), chunk.chunk_id),
        )


def _chunk_from_payload(payload: dict[str, Any], score: float) -> PolicyChunk | None:
    chunk_id = payload.get("chunkId")
    title = payload.get("title")
    heading_path = payload.get("headingPath")
    page = payload.get("page")
    content = payload.get("content")
    document_id = payload.get("documentId")
    document_type = payload.get("documentType")
    version = payload.get("version")
    priority = payload.get("priority")
    checksum = payload.get("checksum")
    if (
        not isinstance(chunk_id, str)
        or not isinstance(title, str)
        or not isinstance(heading_path, list)
        or not all(isinstance(part, str) for part in heading_path)
        or not isinstance(page, int)
        or not isinstance(content, str)
    ):
        return None
    return PolicyChunk(
        chunk_id=chunk_id,
        title=title,
        heading_path=tuple(heading_path),
        page=page,
        content=content,
        score=score,
        document_id=document_id if isinstance(document_id, str) else None,
        document_type=document_type if isinstance(document_type, str) else None,
        version=version if isinstance(version, str) else None,
        priority=priority if isinstance(priority, int) else None,
        checksum=checksum if isinstance(checksum, str) else None,
    )


def _is_non_substantive_policy_chunk(chunk: PolicyChunk) -> bool:
    """Exclude retrieval instructions that cannot prove a business rule."""

    normalized_headings = {
        re.sub(r"\s+", " ", heading.strip().lower()) for heading in chunk.heading_path
    }
    return bool(normalized_headings & NON_SUBSTANTIVE_POLICY_HEADINGS)


def _open_candidates(
    candidates: list[PolicyChunk], selected_chunk_ids: list[str]
) -> list[PolicyChunk]:
    allowed = {candidate.chunk_id: candidate for candidate in candidates}
    if len(selected_chunk_ids) > 3 or any(
        chunk_id not in allowed for chunk_id in selected_chunk_ids
    ):
        raise PolicyRetrievalError("policy chunk is outside this retrieval result")
    return [allowed[chunk_id] for chunk_id in selected_chunk_ids]


def build_policy_retriever(settings: Settings) -> PolicyRetriever:
    return QdrantPolicyRetriever(
        settings=settings,
        embedding_provider=build_embedding_provider(settings),
    )


def _query_hash(query: str) -> str:
    import hashlib

    return hashlib.sha256(query.strip().encode("utf-8")).hexdigest()[:12]
