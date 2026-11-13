"""Document-grounded tutoring and progressive hint generation."""

from __future__ import annotations

from .models import AssistantMode, ChatMessage, Citation, TutorResponse
from .providers import ChatProvider
from .retrieval import InMemoryVectorIndex


class TutorService:
    def __init__(self, chat: ChatProvider, index: InMemoryVectorIndex, retrieval_limit: int = 5) -> None:
        self._chat = chat
        self._index = index
        self._retrieval_limit = retrieval_limit

    def answer(
        self,
        query: str,
        mode: AssistantMode,
        document_ids: set[str],
        history: list[ChatMessage] | None = None,
    ) -> TutorResponse:
        results = self._index.search(query, document_ids, self._retrieval_limit)
        context_parts: list[str] = []
        citations: list[Citation] = []
        for number, result in enumerate(results, start=1):
            source = str(result.chunk.metadata.get("source", "Unknown source"))
            location = result.chunk.metadata.get("location")
            label = f"[{number}] {source}"
            if location:
                label += f", {location}"
            context_parts.append(f"{label}\n{result.chunk.text}")
            citations.append(
                Citation(
                    document_id=result.chunk.document_id,
                    source_name=source,
                    excerpt=result.chunk.text[:240],
                    location=str(location) if location else None,
                )
            )
        history_text = "\n".join(
            f"{message.role}: {message.content}" for message in (history or [])[-8:]
        )
        context_text = "\n\n".join(context_parts) or "No matching course material was found."
        system_prompt = self._system_prompt(mode)
        user_prompt = (
            "Conversation history:\n"
            f"{history_text or 'No previous messages.'}\n\n"
            "Authorized course context:\n"
            f"{context_text}\n\n"
            f"Student question:\n{query}\n\n"
            "Cite supporting passages with their bracketed numbers. "
            "If the context does not support a factual claim, say so."
        )
        return TutorResponse(
            answer=self._chat.complete(system_prompt, user_prompt),
            citations=tuple(citations),
        )

    def next_hint(
        self,
        problem: str,
        history: list[ChatMessage],
        previous_hints: list[str],
    ) -> str:
        history_text = "\n".join(
            f"{message.role}: {message.content}" for message in history[-8:]
        )
        prior = "\n".join(f"- {hint}" for hint in previous_hints) or "None"
        return self._chat.complete(
            "You are a patient Python debugging tutor. Give exactly one new hint. "
            "Do not repeat earlier hints or reveal the complete solution.",
            f"Problem:\n{problem}\n\nConversation:\n{history_text}\n\nEarlier hints:\n{prior}",
        )

    @staticmethod
    def _system_prompt(mode: AssistantMode) -> str:
        if mode == AssistantMode.RUBBER_DUCK:
            return (
                "You are a Python rubber-duck debugging assistant. Ask focused questions "
                "and offer one useful debugging step at a time. Avoid giving the full solution."
            )
        return (
            "You are a Python programming tutor. Explain concepts clearly, include a small "
            "example when helpful, and distinguish course evidence from general knowledge."
        )

