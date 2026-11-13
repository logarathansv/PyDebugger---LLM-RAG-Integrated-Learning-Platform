from __future__ import annotations

from pathlib import Path

import pytest

from pydebugger.models import DocumentChunk
from pydebugger.retrieval import ChromaVectorIndex, EmbeddingMismatchError


class CountingEmbeddings:
    def __init__(self, identity: str = "classroom-embedding-v1") -> None:
        self.embedding_identity = identity
        self.document_calls = 0
        self.query_calls = 0

    @staticmethod
    def _vector(text: str) -> list[float]:
        lowered = text.lower()
        return [
            float(lowered.count("loop")),
            float(lowered.count("exception")),
            1.0,
        ]

    def embed_documents(self, texts):
        self.document_calls += 1
        return [self._vector(text) for text in texts]

    def embed_query(self, text):
        self.query_calls += 1
        return self._vector(text)


def chunk(
    chunk_id: str,
    document_id: str,
    text: str,
    source_name: str,
) -> DocumentChunk:
    return DocumentChunk(
        id=chunk_id,
        document_id=document_id,
        text=text,
        metadata={
            "source_id": document_id,
            "source": source_name,
            "source_name": source_name,
            "source_type": "upload",
            "media_type": "text/plain",
            "sha256": f"hash-{document_id}",
            "stored_filename": source_name,
            "location": "line 1",
            "optional": None,
            "nested": {"unit": 1},
        },
    )


def index(path: Path, embeddings: CountingEmbeddings) -> ChromaVectorIndex:
    return ChromaVectorIndex(embeddings, path, "classroom-content")


def test_chroma_ranks_selected_documents_in_one_global_query(tmp_path: Path):
    embeddings = CountingEmbeddings()
    store = index(tmp_path / "vectors", embeddings)
    store.add(
        [
            chunk("errors-1", "doc-errors", "Python exception handling", "errors.txt"),
            chunk("loops-1", "doc-loops", "loop loop iteration", "loops.txt"),
            chunk("other-1", "doc-other", "unrelated material", "other.txt"),
        ]
    )

    results = store.search(
        "How does a loop work?",
        {"doc-errors", "doc-loops"},
        limit=2,
    )

    assert embeddings.query_calls == 1
    assert [item.chunk.id for item in results] == ["loops-1", "errors-1"]
    assert all(item.chunk.document_id != "doc-other" for item in results)
    assert results[0].score >= results[1].score
    assert results[0].chunk.metadata["nested"] == '{"unit": 1}'


def test_chroma_persists_ids_metadata_and_deletion(tmp_path: Path):
    vector_path = tmp_path / "vectors"
    first = index(vector_path, CountingEmbeddings())
    first.add([chunk("loops-1", "doc-loops", "loop iteration", "loops.txt")])

    reopened = index(vector_path, CountingEmbeddings())
    results = reopened.search("loop", {"doc-loops"}, limit=1)

    assert results[0].chunk.id == "loops-1"
    assert reopened.list_source_metadata()[0]["sha256"] == "hash-doc-loops"
    reopened.remove_document("doc-loops")
    assert reopened.search("loop", {"doc-loops"}, limit=1) == []


def test_chroma_rejects_embedding_identity_mismatch(tmp_path: Path):
    vector_path = tmp_path / "vectors"
    index(vector_path, CountingEmbeddings("embedding-v1"))

    with pytest.raises(EmbeddingMismatchError, match="embedding-v1"):
        index(vector_path, CountingEmbeddings("embedding-v2"))


def test_empty_document_selection_never_searches_entire_collection(tmp_path: Path):
    embeddings = CountingEmbeddings()
    store = index(tmp_path / "vectors", embeddings)
    store.add([chunk("loops-1", "doc-loops", "loop iteration", "loops.txt")])

    assert store.search("loop", set(), limit=5) == []
    assert embeddings.query_calls == 0
