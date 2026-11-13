"""AI provider contracts for Gemini and Azure chat/embedding clients."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from .config import Settings


class ChatProvider(Protocol):
    def complete(self, system_prompt: str, user_prompt: str) -> str: ...


@dataclass(frozen=True)
class ChatCompletion:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    model: str | None = None


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
            max_tokens=settings.llm_max_output_tokens,
            max_retries=3,
            timeout=30,
        )

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        return self.complete_with_usage(system_prompt, user_prompt).text

    def complete_with_usage(
        self, system_prompt: str, user_prompt: str
    ) -> ChatCompletion:
        response = self._client.invoke(
            [("system", system_prompt), ("human", user_prompt)]
        )
        usage = response.usage_metadata or {}
        metadata = response.response_metadata or {}
        return ChatCompletion(
            text=str(response.content),
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            model=str(metadata.get("model_name") or metadata.get("model") or "") or None,
        )


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


class GoogleGeminiChatProvider(ChatProvider):
    """Google AI Studio Gemini chat adapter."""

    def __init__(self, settings: Settings) -> None:
        settings.validate(require_ai=True)
        from langchain_google_genai import ChatGoogleGenerativeAI

        self._client = ChatGoogleGenerativeAI(
            model=settings.google_chat_model,
            api_key=settings.google_api_key,
            max_tokens=settings.llm_max_output_tokens,
            max_retries=3,
            timeout=30,
            vertexai=False,
        )

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        return self.complete_with_usage(system_prompt, user_prompt).text

    def complete_with_usage(
        self, system_prompt: str, user_prompt: str
    ) -> ChatCompletion:
        response = self._client.invoke(
            [("system", system_prompt), ("human", user_prompt)]
        )
        usage = response.usage_metadata or {}
        metadata = response.response_metadata or {}
        text = getattr(response, "text", None) or str(response.content)
        return ChatCompletion(
            text=text,
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            model=str(metadata.get("model_name") or settings_model(metadata) or "") or None,
        )


class GoogleGeminiEmbeddingProvider(EmbeddingProvider):
    """Google AI Studio text embedding adapter for Chroma retrieval."""

    def __init__(self, settings: Settings) -> None:
        settings.validate(require_ai=True)
        from langchain_google_genai import GoogleGenerativeAIEmbeddings

        self._identity = f"google-gemini:{settings.google_embedding_model}"
        self._client = GoogleGenerativeAIEmbeddings(
            model=settings.google_embedding_model,
            google_api_key=settings.google_api_key,
            request_options={"timeout": 30},
            vertexai=False,
        )

    @property
    def embedding_identity(self) -> str:
        return self._identity

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._client.embed_documents(list(texts))

    def embed_query(self, text: str) -> list[float]:
        return self._client.embed_query(text)


def settings_model(metadata: dict) -> str | None:
    """Normalize provider-specific Gemini response model metadata."""
    value = metadata.get("model")
    return str(value) if value else None


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

    def complete_with_usage(
        self, system_prompt: str, user_prompt: str
    ) -> ChatCompletion:
        return self._chat.complete_with_usage(system_prompt, user_prompt)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._embeddings.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._embeddings.embed_query(text)
