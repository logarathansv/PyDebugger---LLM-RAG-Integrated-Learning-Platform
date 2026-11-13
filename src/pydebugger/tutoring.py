"""Grounded tutoring and progressive hint orchestration."""

from __future__ import annotations

import re

from langchain_core.prompts import PromptTemplate

from .hints import HintWorkflow
from .models import AssistantMode, ChatMessage, Citation, HintProgress, TutorResponse
from .providers import ChatProvider
from .retrieval import VectorIndex
from .routing import RequestRoute, RequestRouter

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_STOP_WORDS = {
    "and",
    "are",
    "but",
    "can",
    "for",
    "from",
    "how",
    "that",
    "the",
    "this",
    "what",
    "when",
    "where",
    "which",
    "with",
    "you",
    "your",
}
_CHARS_PER_TOKEN = 4
_ANSWER_PROMPT = PromptTemplate.from_template(
    "History:\n{history}\n\nCourse context:\n{context}\n\n"
    "Question:\n{question}\n\nUse bracketed source numbers. "
    "If evidence is insufficient, say so."
)


def relevant_conversation(
    history: list[ChatMessage],
    focus: str,
    *,
    recent: int = 4,
    max_messages: int = 6,
    max_chars: int = 3000,
) -> str:
    """Keep recent turns plus older turns that overlap the current problem."""
    focus_tokens = {
        token.lower()
        for token in _TOKEN.findall(focus)
        if token.lower() not in _STOP_WORDS
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
        if len(rendered) >= max_messages:
            break
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
        retrieval_limit: int = 3,
        retrieval_min_score: float = 0.25,
        retrieval_context_tokens: int = 2_500,
        conversation_context_tokens: int = 750,
    ) -> None:
        self._chat = chat
        self._index = index
        self._retrieval_limit = retrieval_limit
        self._retrieval_min_score = retrieval_min_score
        self._retrieval_context_tokens = retrieval_context_tokens
        self._conversation_context_tokens = conversation_context_tokens
        self._hints = HintWorkflow(chat)
        self._router = RequestRouter()

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

        route = self._router.route(query)
        context_parts: list[str] = []
        citations: list[Citation] = []
        remaining_chars = self._retrieval_context_tokens * _CHARS_PER_TOKEN
        for number, result in enumerate(results, start=1):
            source = str(result.chunk.metadata.get("source", "Unknown source"))
            location = result.chunk.metadata.get("location")
            label = f"[{number}] {source}"
            if location:
                label += f", {location}"
            label += f" (chunk {result.chunk.id})"
            available = remaining_chars - len(label) - 1
            if available <= 0:
                break
            excerpt = result.chunk.text[:available].strip()
            if not excerpt:
                continue
            context_parts.append(f"{label}\n{excerpt}")
            remaining_chars -= len(label) + len(excerpt) + 1
            citations.append(
                Citation(
                    document_id=result.chunk.document_id,
                    source_name=source,
                    excerpt=excerpt[:240],
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
        history_text = relevant_conversation(
            history or [],
            query,
            max_chars=self._conversation_context_tokens * _CHARS_PER_TOKEN,
        )
        context_text = "\n\n".join(context_parts)
        user_prompt = _ANSWER_PROMPT.format(
            history=history_text or "None",
            context=context_text,
            question=query,
        )
        return TutorResponse(
            answer=self._chat.complete(self._system_prompt(mode, route), user_prompt),
            citations=tuple(citations),
        )

    def next_hint(
        self,
        problem: str,
        history: list[ChatMessage],
        previous_hints: list[str],
    ) -> HintProgress:
        conversation = relevant_conversation(
            history,
            problem,
            max_chars=self._conversation_context_tokens * _CHARS_PER_TOKEN,
        )
        return self._hints.next_hint(problem, history, previous_hints, conversation)

    @staticmethod
    def _system_prompt(mode: AssistantMode, route: RequestRoute) -> str:
        if mode == AssistantMode.RUBBER_DUCK:
            return (
                "You are a programming rubber duck. Use only supplied course context. "
                "Ask one focused question or suggest one diagnostic step. Cite [n]."
            )
        if route is RequestRoute.DEBUG:
            return (
                "You are a programming debugging tutor. Use only supplied course context. "
                "Explain the likely cause and one safe next step. Never execute code. Cite [n]."
            )
        if route is RequestRoute.RAG:
            return "Answer concisely from supplied course context only. Cite [n] for claims."
        return (
            "You are a programming tutor. Teach concisely from supplied course context, "
            "using one small example only when useful. Cite [n]."
        )
