"""Grounded tutoring and progressive hint orchestration."""

from __future__ import annotations

import re

from .hints import HintWorkflow
from .models import AssistantMode, ChatMessage, Citation, HintProgress, TutorResponse
from .providers import ChatProvider
from .retrieval import VectorIndex


_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_STOP_WORDS = {
    "and", "are", "but", "can", "for", "from", "how", "that", "the", "this",
    "what", "when", "where", "which", "with", "you", "your",
}


def relevant_conversation(
    history: list[ChatMessage], focus: str, *, recent: int = 4, max_chars: int = 6000
) -> str:
    """Keep recent turns plus older turns that overlap the current problem."""
    focus_tokens = {
        token.lower() for token in _TOKEN.findall(focus) if token.lower() not in _STOP_WORDS
    }
    recent_start = max(0, len(history) - recent)
    selected: list[tuple[int, ChatMessage]] = []
    for index, message in enumerate(history):
        tokens = {token.lower() for token in _TOKEN.findall(message.content)}
        if index >= recent_start or focus_tokens.intersection(tokens):
            selected.append((index, message))
    rendered: list[str] = []
    used = 0
    for _, message in reversed(selected):
        line = f"{message.role}: {message.content.strip()}"
        if not line.strip() or used + len(line) > max_chars:
            continue
        rendered.append(line)
        used += len(line)
    return "\n".join(reversed(rendered))


class TutorService:
    def __init__(
        self,
        chat: ChatProvider,
        index: VectorIndex,
        retrieval_limit: int = 5,
        retrieval_min_score: float = 0.25,
    ) -> None:
        self._chat = chat
        self._index = index
        self._retrieval_limit = retrieval_limit
        self._retrieval_min_score = retrieval_min_score
        self._hints = HintWorkflow(chat)

    def answer(
        self,
        query: str,
        mode: AssistantMode,
        document_ids: set[str],
        history: list[ChatMessage] | None = None,
    ) -> TutorResponse:
        results = [
            result
            for result in self._index.search(query, document_ids, self._retrieval_limit)
            if result.score >= self._retrieval_min_score
        ]
        if not results:
            return TutorResponse(
                answer=(
                    "I couldn’t find enough supporting evidence in the selected course "
                    "materials to answer that reliably. Try rephrasing the question or "
                    "selecting a document that covers the topic."
                ),
                grounded=False,
            )

        context_parts: list[str] = []
        citations: list[Citation] = []
        for number, result in enumerate(results, start=1):
            source = str(result.chunk.metadata.get("source", "Unknown source"))
            location = result.chunk.metadata.get("location")
            label = f"[{number}] {source}"
            if location:
                label += f", {location}"
            label += f" (chunk {result.chunk.id})"
            context_parts.append(f"{label}\n{result.chunk.text}")
            citations.append(
                Citation(
                    document_id=result.chunk.document_id,
                    source_name=source,
                    excerpt=result.chunk.text[:240],
                    location=str(location) if location else None,
                    chunk_id=result.chunk.id,
                    score=result.score,
                    source_uri=(
                        str(result.chunk.metadata["origin_uri"])
                        if result.chunk.metadata.get("origin_uri")
                        else None
                    ),
                )
            )
        history_text = relevant_conversation(history or [], query)
        context_text = "\n\n".join(context_parts)
        user_prompt = (
            "Relevant conversation history:\n"
            f"{history_text or 'No previous messages.'}\n\n"
            "Authorized course context:\n"
            f"{context_text}\n\n"
            f"Student question:\n{query}\n\n"
            "Cite supporting passages with their bracketed numbers. "
            "If the context does not support a factual claim, say so."
        )
        return TutorResponse(
            answer=self._chat.complete(self._system_prompt(mode), user_prompt),
            citations=tuple(citations),
        )

    def next_hint(
        self,
        problem: str,
        history: list[ChatMessage],
        previous_hints: list[str],
    ) -> HintProgress:
        conversation = relevant_conversation(history, problem)
        return self._hints.next_hint(problem, history, previous_hints, conversation)

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
