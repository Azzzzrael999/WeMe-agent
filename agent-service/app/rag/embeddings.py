"""Embedding providers shared by policy ingestion and retrieval."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Any, Protocol

from app.config import Settings

DETERMINISTIC_VECTOR_SIZE = 64
BGE_M3_VECTOR_SIZE = 1024


class EmbeddingError(RuntimeError):
    """Raised when the configured local embedding model cannot serve vectors."""


class EmbeddingProvider(Protocol):
    @property
    def dimension(self) -> int: ...

    @property
    def model_id(self) -> str: ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


@dataclass(frozen=True)
class DeterministicEmbeddingProvider:
    """Small deterministic fixture provider; never selected by production Compose."""

    dimension: int = DETERMINISTIC_VECTOR_SIZE
    model_id: str = "deterministic-hash-v1"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        normalized = text.lower()
        tokens = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]{1,2}", normalized)
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = digest[0] % self.dimension
            vector[index] += 1.0 if digest[1] & 1 else -1.0
        norm = math.sqrt(sum(value * value for value in vector))
        return vector if norm == 0 else [value / norm for value in vector]


@dataclass
class BgeM3EmbeddingProvider:
    """Lazy, local-only BGE-M3 dense embedding provider."""

    model_path: str
    device: str = "cpu"
    batch_size: int = 4
    max_length: int = 2048
    dimension: int = BGE_M3_VECTOR_SIZE
    model_id: str = "bge-m3-local-dense-v1"
    _model: Any = field(default=None, init=False, repr=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        with self._lock:
            model = self._load_model()
            try:
                encoded = model.encode(
                    texts,
                    batch_size=self.batch_size,
                    normalize_embeddings=True,
                    convert_to_numpy=True,
                    show_progress_bar=False,
                )
                vectors = encoded.tolist()
            except Exception as exc:
                raise EmbeddingError("BGE-M3 dense embedding failed") from exc
        return self._validate_vectors(vectors, expected_count=len(texts))

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        path = Path(self.model_path)
        if not path.is_dir():
            raise EmbeddingError("local BGE-M3 model directory is unavailable")
        try:
            from sentence_transformers import SentenceTransformer

            model = SentenceTransformer(
                str(path),
                device=self.device,
                local_files_only=True,
            )
            model.max_seq_length = self.max_length
            get_dimension = getattr(model, "get_embedding_dimension", None)
            if get_dimension is None:
                get_dimension = model.get_sentence_embedding_dimension
            actual_dimension = get_dimension()
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError("local BGE-M3 model could not be loaded") from exc
        if actual_dimension != self.dimension:
            raise EmbeddingError(
                f"BGE-M3 vector dimension mismatch: expected {self.dimension}, "
                f"received {actual_dimension}"
            )
        self._model = model
        return model

    def _validate_vectors(
        self, vectors: object, *, expected_count: int
    ) -> list[list[float]]:
        if not isinstance(vectors, list) or len(vectors) != expected_count:
            raise EmbeddingError("BGE-M3 returned an invalid vector batch")
        normalized: list[list[float]] = []
        for vector in vectors:
            if not isinstance(vector, list) or len(vector) != self.dimension:
                raise EmbeddingError("BGE-M3 returned an invalid vector dimension")
            row = [float(value) for value in vector]
            if not all(math.isfinite(value) for value in row):
                raise EmbeddingError("BGE-M3 returned a non-finite vector")
            normalized.append(row)
        return normalized


@lru_cache(maxsize=8)
def _cached_embedding_provider(
    provider_name: str,
    model_path: str,
    device: str,
    batch_size: int,
    max_length: int,
) -> EmbeddingProvider:
    if provider_name == "deterministic":
        return DeterministicEmbeddingProvider()
    if provider_name == "bge_m3":
        return BgeM3EmbeddingProvider(
            model_path=model_path,
            device=device,
            batch_size=batch_size,
            max_length=max_length,
        )
    raise EmbeddingError(f"unsupported embedding provider: {provider_name}")


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    return _cached_embedding_provider(
        settings.rag_embedding_provider,
        settings.rag_embedding_model_path,
        settings.rag_embedding_device,
        settings.rag_embedding_batch_size,
        settings.rag_embedding_max_length,
    )
