"""One persistent Chroma retrieval backend with validated embedding contracts."""

from __future__ import annotations

import json
import math
from pathlib import Path
from threading import RLock
from typing import Any, Iterable, Protocol, Sequence

from .models import DocumentChunk, RetrievedChunk
from .providers import EmbeddingProvider

INDEX_SCHEMA_VERSION = "chunks-v2"


class RetrievalError(RuntimeError):
    """Raised when indexing or retrieval cannot safely continue."""


class EmbeddingMismatchError(RetrievalError):
    """Raised when a persisted collection belongs to another embedding model."""


class VectorIndex(Protocol):
    def add(self, chunks: Iterable[DocumentChunk]) -> None: ...

    def remove_document(self, document_id: str) -> None: ...

    def replace_document(
        self, document_id: str, chunks: Iterable[DocumentChunk]
    ) -> None: ...

    def search(
        self,
        query: str,
        document_ids: set[str] | None = None,
        limit: int = 5,
    ) -> list[RetrievedChunk]: ...

    def list_source_metadata(self) -> tuple[dict[str, Any], ...]: ...


class _ProviderEmbeddings:
    """LangChain Embeddings-compatible adapter with dimension and value checks."""

    def __init__(self, provider: EmbeddingProvider) -> None:
        self.provider = provider
        self._dimension: int | None = None
        self._lock = RLock()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors = self.provider.embed_documents(texts)
        if len(vectors) != len(texts):
            raise RetrievalError("Embedding provider returned an unexpected vector count")
        return [self._validate_vector(vector) for vector in vectors]

    def embed_query(self, text: str) -> list[float]:
        return self._validate_vector(self.provider.embed_query(text))

    def _validate_vector(self, vector: Sequence[float]) -> list[float]:
        normalized = [float(value) for value in vector]
        if not normalized:
            raise RetrievalError("Embedding provider returned an empty vector")
        if any(not math.isfinite(value) for value in normalized):
            raise RetrievalError("Embedding provider returned a non-finite vector")
        with self._lock:
            if self._dimension is None:
                self._dimension = len(normalized)
            elif len(normalized) != self._dimension:
                raise RetrievalError(
                    "Embedding provider returned inconsistent vector dimensions"
                )
        return normalized


class ChromaVectorIndex:
    """Supported persistent index; all selected documents are ranked in one query."""

    def __init__(
        self,
        embeddings: EmbeddingProvider,
        persist_directory: Path,
        collection_name: str,
    ) -> None:
        import chromadb
        from chromadb.config import Settings as ChromaSettings
        from langchain_chroma import Chroma

        persist_directory.mkdir(parents=True, exist_ok=True)
        self._embedding_identity = embeddings.embedding_identity
        self._embedding_adapter = _ProviderEmbeddings(embeddings)
        self._lock = RLock()
        self._client = chromadb.PersistentClient(
            path=str(persist_directory),
            settings=ChromaSettings(anonymized_telemetry=False),
        )

        existing_names = {item.name for item in self._client.list_collections()}
        if collection_name in existing_names:
            collection = self._client.get_collection(collection_name)
            collection_metadata = collection.metadata or {}
            stored_identity = collection_metadata.get("embedding_identity")
            if stored_identity != self._embedding_identity:
                raise EmbeddingMismatchError(
                    "The persisted vector collection was created with embedding identity "
                    f"{stored_identity!r}, but configuration requires "
                    f"{self._embedding_identity!r}. Use a new collection name or rebuild "
                    "the existing collection."
                )
            stored_schema = collection_metadata.get("index_schema_version")
            if stored_schema != INDEX_SCHEMA_VERSION:
                raise RetrievalError(
                    "The persisted vector collection uses index schema "
                    f"{stored_schema!r}, but this application requires "
                    f"{INDEX_SCHEMA_VERSION!r}. Use a new collection name or rebuild it."
                )
        else:
            collection = self._client.create_collection(
                collection_name,
                metadata={
                    "embedding_identity": self._embedding_identity,
                    "index_schema_version": INDEX_SCHEMA_VERSION,
                    "hnsw:space": "cosine",
                },
            )

        self._store = Chroma(
            client=self._client,
            collection_name=collection_name,
            embedding_function=self._embedding_adapter,
            relevance_score_fn=self._cosine_relevance,
            create_collection_if_not_exists=False,
        )

    def add(self, chunks: Iterable[DocumentChunk]) -> None:
        from langchain_core.documents import Document

        items = list(chunks)
        if not items:
            return
        documents = [
            Document(
                id=item.id,
                page_content=item.text,
                metadata=self._metadata_for_chroma(item),
            )
            for item in items
        ]
        try:
            with self._lock:
                self._store.add_documents(
                    documents=documents,
                    ids=[item.id for item in items],
                )
        except Exception as error:
            raise RetrievalError(
                "Could not index chunks. Verify the embedding deployment and vector "
                "collection use the same model and dimensions."
            ) from error

    def remove_document(self, document_id: str) -> None:
        with self._lock:
            result = self._store.get(
                where={"document_id": document_id},
                include=[],
            )
            ids = list(result.get("ids") or [])
            if ids:
                self._store.delete(ids=ids)

    def replace_document(
        self, document_id: str, chunks: Iterable[DocumentChunk]
    ) -> None:
        items = list(chunks)
        if not items or any(item.document_id != document_id for item in items):
            raise RetrievalError("Replacement chunks must belong to the target document")
        new_ids = {item.id for item in items}
        with self._lock:
            existing = self._store.get(
                where={"document_id": document_id}, include=[]
            )
            old_ids = set(existing.get("ids") or [])
            try:
                self.add(items)
            except Exception:
                created_ids = new_ids - old_ids
                if created_ids:
                    self._store.delete(ids=sorted(created_ids))
                raise
            obsolete_ids = old_ids - new_ids
            if obsolete_ids:
                self._store.delete(ids=sorted(obsolete_ids))

    def search(
        self,
        query: str,
        document_ids: set[str] | None = None,
        limit: int = 5,
    ) -> list[RetrievedChunk]:
        cleaned_query = query.strip()
        if not cleaned_query:
            raise ValueError("Retrieval query cannot be empty")
        if limit < 1:
            raise ValueError("Retrieval limit must be at least one")
        if document_ids == set():
            return []

        where: dict[str, Any] | None = None
        if document_ids:
            sorted_ids = sorted(document_ids)
            where = (
                {"document_id": sorted_ids[0]}
                if len(sorted_ids) == 1
                else {"document_id": {"$in": sorted_ids}}
            )
        try:
            with self._lock:
                matches = self._store.similarity_search_with_relevance_scores(
                    cleaned_query,
                    k=limit,
                    filter=where,
                )
        except Exception as error:
            raise RetrievalError(
                "Vector search failed. Verify the configured embedding deployment still "
                "matches the persisted collection."
            ) from error

        results: list[RetrievedChunk] = []
        for document, score in matches:
            metadata = dict(document.metadata)
            chunk_id = str(metadata.get("chunk_id") or document.id or "")
            document_id = str(metadata.get("document_id") or "")
            if not chunk_id or not document_id:
                raise RetrievalError("Stored vector record is missing traceability metadata")
            results.append(
                RetrievedChunk(
                    chunk=DocumentChunk(
                        id=chunk_id,
                        document_id=document_id,
                        text=document.page_content,
                        metadata=metadata,
                    ),
                    score=max(0.0, min(1.0, float(score))),
                )
            )
        return results

    def list_source_metadata(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            result = self._store.get(include=["metadatas"])
        sources: dict[str, dict[str, Any]] = {}
        for metadata in result.get("metadatas") or []:
            if not metadata:
                continue
            source_id = str(metadata.get("source_id") or "")
            if source_id and source_id not in sources:
                sources[source_id] = dict(metadata)
        return tuple(sources[key] for key in sorted(sources))

    @staticmethod
    def _metadata_for_chroma(chunk: DocumentChunk) -> dict[str, str | int | float | bool]:
        metadata: dict[str, str | int | float | bool] = {
            "chunk_id": chunk.id,
            "document_id": chunk.document_id,
        }
        for key, value in chunk.metadata.items():
            if value is None:
                continue
            if isinstance(value, bool | str | int | float):
                metadata[key] = value
            else:
                metadata[key] = json.dumps(value, sort_keys=True, ensure_ascii=False)
        return metadata

    @staticmethod
    def _cosine_relevance(distance: float) -> float:
        # Chroma cosine distance is 0 for identical and can reach 2 for opposite.
        return max(0.0, min(1.0, 1.0 - float(distance) / 2.0))


class InMemoryVectorIndex:
    """Deterministic test double; the application does not select this backend."""

    def __init__(self, embeddings: EmbeddingProvider) -> None:
        self._embeddings = _ProviderEmbeddings(embeddings)
        self._entries: dict[str, tuple[DocumentChunk, list[float]]] = {}
        self._lock = RLock()

    def add(self, chunks: Iterable[DocumentChunk]) -> None:
        items = list(chunks)
        if not items:
            return
        vectors = self._embeddings.embed_documents([item.text for item in items])
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

    def replace_document(
        self, document_id: str, chunks: Iterable[DocumentChunk]
    ) -> None:
        items = list(chunks)
        if not items or any(item.document_id != document_id for item in items):
            raise RetrievalError("Replacement chunks must belong to the target document")
        vectors = self._embeddings.embed_documents([item.text for item in items])
        replacement = dict(zip((item.id for item in items), zip(items, vectors), strict=True))
        with self._lock:
            self._entries = {
                key: value
                for key, value in self._entries.items()
                if value[0].document_id != document_id
            }
            self._entries.update(replacement)

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

    def list_source_metadata(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            entries = list(self._entries.values())
        sources: dict[str, dict[str, Any]] = {}
        for chunk, _ in entries:
            source_id = str(chunk.metadata.get("source_id") or chunk.document_id)
            sources.setdefault(source_id, dict(chunk.metadata))
        return tuple(sources[key] for key in sorted(sources))

    @staticmethod
    def _cosine(left: list[float], right: list[float]) -> float:
        if len(left) != len(right):
            raise RetrievalError("Embedding dimensions do not match")
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if not left_norm or not right_norm:
            return 0.0
        similarity = sum(a * b for a, b in zip(left, right, strict=True)) / (
            left_norm * right_norm
        )
        return max(0.0, min(1.0, (similarity + 1.0) / 2.0))
