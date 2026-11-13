from __future__ import annotations

from pathlib import Path

import pytest

from pydebugger import AssistantMode
from pydebugger.engine import PyDebuggerEngine
from pydebugger.execution import DisabledExecutor
from pydebugger.models import ChatMessage, DocumentChunk, RetrievedChunk
from pydebugger.retrieval import InMemoryVectorIndex, RetrievalError
from pydebugger.tutoring import TutorService, relevant_conversation

from test_core_engine import FakeAIProvider, make_settings


class FailingReplacementIndex(InMemoryVectorIndex):
    fail = False

    def replace_document(self, document_id, chunks):
        if self.fail:
            raise RetrievalError("replacement failed")
        super().replace_document(document_id, chunks)


class StaticIndex:
    def __init__(self, results):
        self.results = results

    def search(self, query, document_ids=None, limit=5):
        return self.results[:limit]


class CountingChat(FakeAIProvider):
    def __init__(self, responses=None):
        super().__init__()
        self.responses = list(responses or ["A grounded answer [1]."])
        self.calls = 0

    def complete(self, system_prompt, user_prompt):
        self.calls += 1
        self.last_system = system_prompt
        self.last_user = user_prompt
        return self.responses[min(self.calls - 1, len(self.responses) - 1)]


def test_upload_replacement_keeps_id_and_invalidates_old_chunks(tmp_path: Path):
    provider = FakeAIProvider()
    index = InMemoryVectorIndex(provider)
    engine = PyDebuggerEngine(
        make_settings(tmp_path), provider, provider, DisabledExecutor(), vector_index=index
    )

    first = engine.add_document("lesson.txt", b"old exception material")
    old_path = first.path
    second = engine.add_document("lesson.txt", b"new loop material")

    assert second.id == first.id
    assert second.sha256 != first.sha256
    assert second.index_version != first.index_version
    assert not old_path.exists()
    results = index.search("loop", {second.id}, 10)
    assert results and all(second.index_version in item.chunk.id for item in results)
    assert all("old exception" not in item.chunk.text for item in results)


def test_identical_upload_is_idempotent(tmp_path: Path):
    provider = FakeAIProvider()
    engine = PyDebuggerEngine(
        make_settings(tmp_path), provider, provider, DisabledExecutor(),
        vector_index=InMemoryVectorIndex(provider),
    )
    first = engine.add_document("lesson.txt", b"same lesson")
    second = engine.add_document("lesson.txt", b"same lesson")

    assert second is first
    assert list(first.path.parent.iterdir()) == [first.path]


def test_failed_replacement_preserves_previous_revision(tmp_path: Path):
    provider = FakeAIProvider()
    index = FailingReplacementIndex(provider)
    engine = PyDebuggerEngine(
        make_settings(tmp_path), provider, provider, DisabledExecutor(), vector_index=index
    )
    first = engine.add_document("lesson.txt", b"working loop lesson")
    index.fail = True

    with pytest.raises(RetrievalError, match="replacement failed"):
        engine.add_document("lesson.txt", b"broken replacement")

    assert engine.list_documents() == (first,)
    assert first.path.exists()
    assert len(list(first.path.parent.iterdir())) == 1
    assert index.search("loop", {first.id}, 1)[0].chunk.text == "working loop lesson"


def test_persistent_catalog_restores_and_delete_cleans_storage(tmp_path: Path):
    settings = make_settings(tmp_path)
    provider = FakeAIProvider()
    first_engine = PyDebuggerEngine(settings, provider, provider, DisabledExecutor())
    source = first_engine.add_document("lesson.txt", b"persistent loop lesson")

    reopened = PyDebuggerEngine(settings, provider, provider, DisabledExecutor())
    assert reopened.list_documents()[0].id == source.id
    assert reopened.list_documents()[0].index_version == source.index_version
    document_dir = source.path.parent
    assert reopened.remove_document(source.id)
    assert not document_dir.exists()
    assert reopened.index.search("loop", {source.id}, 5) == []


def test_no_evidence_response_does_not_call_chat_model():
    chat = CountingChat()
    weak = RetrievedChunk(DocumentChunk("c", "d", "unrelated", {"source": "x"}), 0.1)
    tutor = TutorService(chat, StaticIndex([weak]), retrieval_min_score=0.5)

    response = tutor.answer("Explain decorators", AssistantMode.PROGRAMMING_TUTOR, {"d"})

    assert not response.grounded
    assert response.citations == ()
    assert chat.calls == 0


def test_relevant_history_keeps_matching_old_turn_and_recent_turns():
    history = [
        ChatMessage("user", "My loop counter never changes"),
        ChatMessage("assistant", "unrelated database discussion"),
        ChatMessage("user", "recent one"),
        ChatMessage("assistant", "recent two"),
        ChatMessage("user", "recent three"),
        ChatMessage("assistant", "recent four"),
    ]
    context = relevant_conversation(history, "Why is my loop counter stuck?")

    assert "loop counter never changes" in context
    assert "database discussion" not in context
    assert "recent four" in context


def test_hint_progression_retries_duplicate_and_advances_level():
    chat = CountingChat(["Inspect the loop condition.", "Inspect the loop condition.", "Print the counter type."])
    tutor = TutorService(chat, StaticIndex([]))
    first = tutor.next_hint("My loop is stuck", [], [])
    second = tutor.next_hint("My loop is stuck", [], list(first.hints))

    assert first.level == 1
    assert second.level == 2
    assert second.hint == "Print the counter type."
    assert len(second.hints) == 2
    assert chat.calls == 3
