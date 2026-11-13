"""AI provider contracts and the supported Azure OpenAI implementation."""

from __future__ import annotations

from typing import Protocol, Sequence

from .config import Settings


class ChatProvider(Protocol):
    def complete(self, system_prompt: str, user_prompt: str) -> str: ...


class EmbeddingProvider(Protocol):
    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class AzureOpenAIProvider(ChatProvider, EmbeddingProvider):
    """Thin adapter that keeps LangChain out of the domain services."""

    def __init__(self, settings: Settings) -> None:
        settings.validate(require_ai=True)
        from langchain_openai import AzureChatOpenAI, AzureOpenAIEmbeddings

        common = {
            "azure_endpoint": settings.azure_openai_endpoint,
            "api_key": settings.azure_openai_api_key,
            "openai_api_version": settings.azure_openai_api_version,
        }
        self._chat = AzureChatOpenAI(
            azure_deployment=settings.azure_chat_deployment,
            temperature=0.2,
            max_retries=3,
            timeout=30,
            **common,
        )
        self._embeddings = AzureOpenAIEmbeddings(
            azure_deployment=settings.azure_embedding_deployment,
            **common,
        )

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        response = self._chat.invoke(
            [("system", system_prompt), ("human", user_prompt)]
        )
        return str(response.content)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._embeddings.embed_documents(list(texts))

    def embed_query(self, text: str) -> list[float]:
        return self._embeddings.embed_query(text)

