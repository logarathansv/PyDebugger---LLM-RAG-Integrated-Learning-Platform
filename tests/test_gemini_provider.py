from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from pydebugger.config import AIProvider
from pydebugger.providers import (
    GoogleGeminiChatProvider,
    GoogleGeminiEmbeddingProvider,
)

from test_core_engine import make_settings


def gemini_settings(tmp_path):
    return replace(
        make_settings(tmp_path),
        ai_provider=AIProvider.GEMINI,
        google_api_key="test-google-key",
        google_chat_model="gemini-3.5-flash-lite",
        google_embedding_model="gemini-embedding-001",
        azure_openai_endpoint=None,
        azure_openai_api_key=None,
        azure_chat_deployment=None,
        azure_embedding_deployment=None,
    )


class FakeChatClient:
    def invoke(self, messages):
        assert messages[0][0] == "system"
        assert messages[1][0] == "human"
        return SimpleNamespace(
            text="Use a loop.",
            content="Use a loop.",
            usage_metadata={"input_tokens": 12, "output_tokens": 4},
            response_metadata={"model_name": "gemini-3.5-flash-lite"},
        )


class FakeEmbeddingClient:
    def embed_documents(self, texts):
        return [[float(len(text)), 1.0] for text in texts]

    def embed_query(self, text):
        return [float(len(text)), 1.0]


def test_gemini_chat_returns_usage_metadata(tmp_path):
    provider = GoogleGeminiChatProvider(gemini_settings(tmp_path))
    provider._client = FakeChatClient()

    result = provider.complete_with_usage("Tutor safely", "Explain loops")

    assert result.text == "Use a loop."
    assert result.input_tokens == 12
    assert result.output_tokens == 4
    assert result.model == "gemini-3.5-flash-lite"


def test_gemini_embeddings_have_stable_identity(tmp_path):
    provider = GoogleGeminiEmbeddingProvider(gemini_settings(tmp_path))
    provider._client = FakeEmbeddingClient()

    assert provider.embedding_identity == "google-gemini:gemini-embedding-001"
    assert len(provider.embed_documents(["one", "two"])) == 2
    assert provider.embed_query("loop") == [4.0, 1.0]
