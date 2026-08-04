"""A tiny, real Qdrant-backed policy retriever for Day 4.

The deterministic hash embedding is intentionally small and local: it keeps
fixture execution reproducible and avoids downloading a model.  It is only a
retrieval implementation; it is not an extra Agent and it never produces a
business decision on its own.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from qdrant_client import QdrantClient, models

from app.config import Settings
from app.schemas.agent import Citation

VECTOR_SIZE = 64
MAX_RERANK_CANDIDATES = 200


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
        content="需要演示材料时应选择具备LARGE_SCREEN设备的会议室；白板需求应作为房间硬约束。",
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


def deterministic_embedding(text: str) -> list[float]:
    """Stable signed hashing vector suitable only for the tiny Day 4 corpus."""

    vector = [0.0] * VECTOR_SIZE
    normalized = text.lower()
    tokens = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]{1,2}", normalized)
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = digest[0] % VECTOR_SIZE
        vector[index] += 1.0 if digest[1] & 1 else -1.0
    norm = math.sqrt(sum(value * value for value in vector))
    return vector if norm == 0 else [value / norm for value in vector]


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


def _point_id(chunk_id: str) -> int:
    return int.from_bytes(hashlib.sha256(chunk_id.encode("utf-8")).digest()[:8], "big") >> 1


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
    client: QdrantClient | None = None
    _seeded: bool = field(default=False, init=False)

    def _qdrant(self) -> QdrantClient:
        if self.client is None:
            self.client = QdrantClient(url=self.settings.qdrant_url, timeout=5)
        return self.client

    def ensure_seeded(self) -> None:
        if self._seeded:
            return
        client = self._qdrant()
        try:
            if not client.collection_exists(self.settings.qdrant_collection):
                client.create_collection(
                    collection_name=self.settings.qdrant_collection,
                    vectors_config=models.VectorParams(
                        size=VECTOR_SIZE, distance=models.Distance.COSINE
                    ),
                )
            client.upsert(
                collection_name=self.settings.qdrant_collection,
                wait=True,
                points=[
                    models.PointStruct(
                        id=_point_id(chunk.chunk_id),
                        vector=deterministic_embedding(f"{chunk.title} {chunk.content}"),
                        payload={
                            "chunkId": chunk.chunk_id,
                            "title": chunk.title,
                            "headingPath": list(chunk.heading_path),
                            "page": chunk.page,
                            "content": chunk.content,
                            "source": "BUILT_IN_SEED",
                        },
                    )
                    for chunk in SEED_CHUNKS
                ],
            )
        except Exception as exc:  # Qdrant client has several transport exception classes.
            raise PolicyRetrievalError("Qdrant policy corpus is unavailable") from exc
        self._seeded = True

    def search(self, query: str, limit: int = 5) -> list[PolicyChunk]:
        self.ensure_seeded()
        candidate_limit = min(MAX_RERANK_CANDIDATES, max(limit, limit * 20))
        try:
            result = self._qdrant().query_points(
                collection_name=self.settings.qdrant_collection,
                query=deterministic_embedding(query),
                limit=candidate_limit,
                with_payload=True,
            )
        except Exception as exc:  # Qdrant client has several transport exception classes.
            raise PolicyRetrievalError("Qdrant policy search failed") from exc
        chunks: list[PolicyChunk] = []
        for point in result.points:
            payload = point.payload
            if not isinstance(payload, dict):
                continue
            chunk = _chunk_from_payload(payload, float(point.score))
            if chunk is not None:
                chunks.append(chunk)
        return sorted(
            chunks,
            key=lambda chunk: (
                -(_lexical_score(query, chunk) + chunk.score),
                chunk.chunk_id,
            ),
        )[:limit]

    def open_candidates(
        self, *, candidates: list[PolicyChunk], selected_chunk_ids: list[str]
    ) -> list[PolicyChunk]:
        return _open_candidates(candidates, selected_chunk_ids)


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
    return QdrantPolicyRetriever(settings)
