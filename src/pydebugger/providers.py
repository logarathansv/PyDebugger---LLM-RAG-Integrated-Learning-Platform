"""AI provider contracts with separate Azure chat and embedding clients."""

from __future__ import annotations

from typing import Protocol, Sequence

from .config import Settings


class ChatProvider(Protocol):
    def complete(self, system_prompt: str, user_prompt: str) -> str: ...


class EmbeddingProvider(Protocol):
    @property
    def embedding_identity(self) -> str: ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class AzureOpenAIChatProvider(ChatProvider):
    """Azure chat adapter bound only to the configured chat deployment."""

    def __init__(self, settings: Settings) -> None:
        settings.validate(require_ai=True)
        from langchain_openai import AzureChatOpenAI

        self._client = AzureChatOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
            azure_deployment=settings.azure_chat_deployment,
            temperature=0.2,
            max_retries=3,
            timeout=30,
        )

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        response = self._client.invoke(
            [("system", system_prompt), ("human", user_prompt)]
        )
        return str(response.content)


class AzureOpenAIEmbeddingProvider(EmbeddingProvider):
    """Azure embedding adapter bound only to the configured embedding deployment."""

    def __init__(self, settings: Settings) -> None:
        settings.validate(require_ai=True)
        from langchain_openai import AzureOpenAIEmbeddings

        self._identity = (
            "azure-openai:"
            f"{settings.azure_embedding_deployment}:"
            f"{settings.azure_openai_api_version}"
        )
        self._client = AzureOpenAIEmbeddings(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
            azure_deployment=settings.azure_embedding_deployment,
            max_retries=3,
            request_timeout=30,
        )

    @property
    def embedding_identity(self) -> str:
        return self._identity

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._client.embed_documents(list(texts))

    def embed_query(self, text: str) -> list[float]:
        return self._client.embed_query(text)


class AzureOpenAIProvider(ChatProvider, EmbeddingProvider):
    """Compatibility facade; new composition should use the separate adapters."""

    def __init__(self, settings: Settings) -> None:
        self._chat = AzureOpenAIChatProvider(settings)
        self._embeddings = AzureOpenAIEmbeddingProvider(settings)

    @property
    def embedding_identity(self) -> str:
        return self._embeddings.embedding_identity

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        return self._chat.complete(system_prompt, user_prompt)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._embeddings.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._embeddings.embed_query(text)
