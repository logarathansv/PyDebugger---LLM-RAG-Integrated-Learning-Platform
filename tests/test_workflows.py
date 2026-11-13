from __future__ import annotations

from pydebugger.models import AssistantMode, DocumentChunk, RetrievedChunk
from pydebugger.routing import RequestRoute, RequestRouter
from pydebugger.tutoring import TutorService


class RecordingChat:
    def __init__(self) -> None:
        self.calls = 0
        self.system = ""
        self.user = ""

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.calls += 1
        self.system = system_prompt
        self.user = user_prompt
        return "Grounded answer [1]."


class RecordingIndex:
    def __init__(self, results: list[RetrievedChunk]) -> None:
        self.results = results
        self.limit = 0

    def search(self, query: str, document_ids=None, limit: int = 5):
        self.limit = limit
        return self.results[:limit]


def test_request_router_uses_rules_without_an_llm_call():
    router = RequestRouter()

    assert router.route("Traceback: NameError on line 4") is RequestRoute.DEBUG
    assert router.route("Explain how a loop works") is RequestRoute.TUTOR
    assert router.route("List the commands in chapter two") is RequestRoute.RAG


def test_tutor_bounds_retrieval_context_and_uses_one_model_call():
    chat = RecordingChat()
    results = [
        RetrievedChunk(
            DocumentChunk(
                f"chunk-{number}",
                "document-1",
                f"marker-{number} " + ("content " * 200),
                {"source": "lesson.txt"},
            ),
            0.9,
        )
        for number in range(5)
    ]
    index = RecordingIndex(results)
    tutor = TutorService(
        chat,
        index,
        retrieval_limit=3,
        retrieval_context_tokens=64,
        conversation_context_tokens=32,
    )

    response = tutor.answer(
        "Explain this lesson",
        AssistantMode.PROGRAMMING_TUTOR,
        {"document-1"},
    )

    assert chat.calls == 1
    assert index.limit == 3
    assert len(response.citations) == 1
    assert "marker-0" in chat.user
    assert "marker-1" not in chat.user
    assert len(chat.user) < 600
