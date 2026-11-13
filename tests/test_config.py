from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from pydebugger.config import AIProvider, ConfigurationError, ExecutionBackend, Settings


def valid_settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        vector_dir=tmp_path / "vectors",
        vector_collection="pydebugger-test-content",
        ai_provider=AIProvider.AZURE_OPENAI,
        google_api_key=None,
        google_chat_model="gemini-test",
        google_embedding_model="embedding-test",
        monitoring_db_path=tmp_path / "monitoring.sqlite3",
        chat_input_cost_per_million=1.0,
        chat_output_cost_per_million=2.0,
        embedding_cost_per_million=0.1,
        azure_openai_endpoint="https://example.openai.azure.com/",
        azure_openai_api_key="test-key",
        azure_chat_deployment="chat",
        azure_embedding_deployment="embedding",
        azure_openai_api_version="2024-10-21",
        execution_backend=ExecutionBackend.DISABLED,
        execution_timeout_seconds=5,
        execution_max_attempts=2,
        execution_output_limit=20_000,
        execution_source_limit=100_000,
        execution_memory_mb=128,
        execution_cpus=0.5,
        execution_pids_limit=32,
        execution_tmpfs_mb=16,
        docker_binary="docker",
        docker_image="python@sha256:abc",
        docker_runtime=None,
        upload_limit_bytes=10_000,
        background_workers=2,
        background_queue_size=8,
        ingestion_timeout_seconds=10,
        ingestion_max_attempts=3,
        retrieval_limit=3,
        retrieval_min_score=0.25,
        retrieval_context_tokens=2_500,
        conversation_context_tokens=750,
        llm_max_output_tokens=600,
    )


def test_settings_hide_api_key_from_repr(tmp_path: Path):
    settings = valid_settings(tmp_path)

    assert "test-key" not in repr(settings)


def test_settings_reject_deployment_endpoint(tmp_path: Path):
    settings = replace(
        valid_settings(tmp_path),
        azure_openai_endpoint=(
            "https://example.openai.azure.com/openai/deployments/chat/completions"
        ),
    )

    with pytest.raises(ConfigurationError, match="resource base URL"):
        settings.validate()


def test_settings_reject_mutable_docker_image(tmp_path: Path):
    settings = replace(
        valid_settings(tmp_path),
        execution_backend=ExecutionBackend.DOCKER,
        docker_image="python:3.13-slim",
    )

    with pytest.raises(ConfigurationError, match="immutable sha256 digest"):
        settings.validate()


def test_from_env_reports_invalid_numeric_value(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("PYDEBUGGER_EXECUTION_MEMORY_MB", "a-lot")

    with pytest.raises(ConfigurationError, match="must be an integer"):
        Settings.from_env(tmp_path, require_ai=False)


def test_settings_reject_negative_model_cost(tmp_path: Path):
    settings = replace(valid_settings(tmp_path), chat_input_cost_per_million=-1)

    with pytest.raises(ConfigurationError, match="CHAT_INPUT_COST"):
        settings.validate()


def test_gemini_settings_require_key_but_not_azure(tmp_path: Path):
    settings = replace(
        valid_settings(tmp_path),
        ai_provider=AIProvider.GEMINI,
        google_api_key="google-test-key",
        azure_openai_endpoint=None,
        azure_openai_api_key=None,
        azure_chat_deployment=None,
        azure_embedding_deployment=None,
    )

    settings.validate()


def test_gemini_settings_reject_missing_key(tmp_path: Path):
    settings = replace(
        valid_settings(tmp_path), ai_provider=AIProvider.GEMINI, google_api_key=None
    )

    with pytest.raises(ConfigurationError, match="GOOGLE_API_KEY"):
        settings.validate()
