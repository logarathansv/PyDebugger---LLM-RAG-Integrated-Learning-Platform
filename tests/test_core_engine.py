from __future__ import annotations

from pathlib import Path

import pytest

from pydebugger import AssistantMode, Settings
from pydebugger.engine import PyDebuggerEngine
from pydebugger.execution import DisabledExecutor, LocalSubprocessExecutor
from pydebugger.ingestion import DocumentIngestor
from pydebugger.models import ChatMessage, DocumentSource
from pydebugger.retrieval import InMemoryVectorIndex
from pydebugger.tutoring import TutorService


class FakeAIProvider:
    def __init__(self) -> None:
        self.last_system = ""
        self.last_user = ""

    @staticmethod
    def _vector(text: str) -> list[float]:
        lowered = text.lower()
        return [float(lowered.count("loop")), float(lowered.count("exception")), 1.0]

    def embed_documents(self, texts):
        return [self._vector(text) for text in texts]

    def embed_query(self, text):
        return self._vector(text)

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.last_system = system_prompt
        self.last_user = user_prompt
        return "Use a loop and check source [1]."


def make_settings(tmp_path: Path, allow_execution: bool = False) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        azure_openai_endpoint=None,
        azure_openai_api_key=None,
        azure_chat_deployment=None,
        azure_embedding_deployment=None,
        azure_openai_api_version="test",
        allow_local_execution=allow_execution,
        execution_timeout_seconds=0.2,
        execution_output_limit=100,
        upload_limit_bytes=10_000,
        retrieval_limit=3,
    )


def test_text_ingestion_preserves_source_metadata(tmp_path: Path):
    path = tmp_path / "loops.txt"
    path.write_text("A for loop repeats a block of Python code.", encoding="utf-8")
    source = DocumentSource("doc-1", path.name, path, "text/plain", "hash")

    chunks = DocumentIngestor(chunk_size=40, chunk_overlap=5).ingest(source)

    assert chunks
    assert all(chunk.document_id == source.id for chunk in chunks)
    assert all(chunk.metadata["source"] == path.name for chunk in chunks)


def test_rst_ingestion_does_not_insert_external_files(tmp_path: Path):
    private_file = tmp_path / "private.txt"
    private_file.write_text("must not be indexed", encoding="utf-8")
    path = tmp_path / "notes.rst"
    path.write_text(
        "Course notes\n============\n\n.. include:: private.txt\n",
        encoding="utf-8",
    )
    source = DocumentSource("doc-rst", path.name, path, "text/x-rst", "hash")

    chunks = DocumentIngestor().ingest(source)

    combined = "\n".join(chunk.text for chunk in chunks)
    assert "must not be indexed" not in combined


def test_csv_ingestion_produces_document_chunks(tmp_path: Path):
    path = tmp_path / "errors.csv"
    path.write_text("error,meaning\nTypeError,wrong type\n", encoding="utf-8")
    source = DocumentSource("doc-csv", path.name, path, "text/csv", "hash")

    chunks = DocumentIngestor().ingest(source)

    assert "TypeError | wrong type" in chunks[0].text


def test_retrieval_ranks_across_documents():
    provider = FakeAIProvider()
    index = InMemoryVectorIndex(provider)
    from pydebugger.models import DocumentChunk

    index.add(
        [
            DocumentChunk("a", "doc-a", "Python exceptions", {"source": "errors.txt"}),
            DocumentChunk("b", "doc-b", "A loop repeats work loop", {"source": "loops.txt"}),
        ]
    )

    results = index.search("How does a loop work?", {"doc-a", "doc-b"}, limit=2)

    assert results[0].chunk.document_id == "doc-b"


def test_tutor_uses_history_and_returns_citations():
    provider = FakeAIProvider()
    index = InMemoryVectorIndex(provider)
    from pydebugger.models import DocumentChunk

    index.add([DocumentChunk("a", "doc-a", "A loop repeats work", {"source": "loops.txt"})])
    tutor = TutorService(provider, index)

    response = tutor.answer(
        "Can you explain the loop?",
        AssistantMode.PROGRAMMING_TUTOR,
        {"doc-a"},
        [ChatMessage("user", "I am learning iteration")],
    )

    assert response.citations[0].source_name == "loops.txt"
    assert "I am learning iteration" in provider.last_user
    assert "[1] loops.txt" in provider.last_user


def test_engine_indexes_and_removes_document(tmp_path: Path):
    provider = FakeAIProvider()
    engine = PyDebuggerEngine(
        make_settings(tmp_path), provider, provider, DisabledExecutor()
    )

    source = engine.add_document("notes.txt", b"Python loop notes")
    assert engine.list_documents() == (source,)
    assert source.path.exists()

    assert engine.remove_document(source.id)
    assert not source.path.exists()
    assert engine.list_documents() == ()


def test_execution_is_disabled_by_default(tmp_path: Path):
    provider = FakeAIProvider()
    engine = PyDebuggerEngine(make_settings(tmp_path), provider, provider)

    result = engine.execute("print('hello')")

    assert result.disabled_reason


def test_local_executor_captures_syntax_errors():
    result = LocalSubprocessExecutor(timeout_seconds=1).execute("def broken(:\n    pass")

    assert result.exit_code != 0
    assert "SyntaxError" in result.stderr


def test_local_executor_times_out():
    result = LocalSubprocessExecutor(timeout_seconds=0.1).execute("while True:\n    pass")

    assert result.timed_out

