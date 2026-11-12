"""Vector retrieval independent of UI and AI vendor SDKs."""

from __future__ import annotations

import math
from threading import RLock
from typing import Iterable

from .models import DocumentChunk, RetrievedChunk
from .providers import EmbeddingProvider


class InMemoryVectorIndex:
    """Pilot index. The interface can later be backed by pgvector or another store."""

    def __init__(self, embeddings: EmbeddingProvider) -> None:
        self._embeddings = embeddings
        self._entries: dict[str, tuple[DocumentChunk, list[float]]] = {}
        self._lock = RLock()

    def add(self, chunks: Iterable[DocumentChunk]) -> None:
        items = list(chunks)
        if not items:
            return
        vectors = self._embeddings.embed_documents([item.text for item in items])
        if len(vectors) != len(items):
            raise ValueError("Embedding provider returned an unexpected vector count")
        with self._lock:
            for item, vector in zip(items, vectors, strict=True):
                self._entries[item.id] = (item, vector)

    def remove_document(self, document_id: str) -> None:
        with self._lock:
            matching = [
                chunk_id
                for chunk_id, (chunk, _) in self._entries.items()
                if chunk.document_id == document_id
            ]
            for chunk_id in matching:
                del self._entries[chunk_id]

    def search(
        self,
        query: str,
        document_ids: set[str] | None = None,
        limit: int = 5,
    ) -> list[RetrievedChunk]:
        query_vector = self._embeddings.embed_query(query)
        with self._lock:
            candidates = list(self._entries.values())
        ranked = [
            RetrievedChunk(chunk=chunk, score=self._cosine(query_vector, vector))
            for chunk, vector in candidates
            if document_ids is None or chunk.document_id in document_ids
        ]
        ranked.sort(key=lambda item: item.score, reverse=True)
        return ranked[:limit]

    @staticmethod
    def _cosine(left: list[float], right: list[float]) -> float:
        if len(left) != len(right):
            raise ValueError("Embedding dimensions do not match")
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if not left_norm or not right_norm:
            return 0.0
        return sum(a * b for a, b in zip(left, right, strict=True)) / (
            left_norm * right_norm
        )

