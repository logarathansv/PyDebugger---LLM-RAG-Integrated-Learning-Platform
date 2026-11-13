from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from pydebugger.config import ConfigurationError, ExecutionBackend, Settings


def valid_settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        vector_dir=tmp_path / "vectors",
        vector_collection="pydebugger-test-content",
        azure_openai_endpoint="https://example.openai.azure.com/",
        azure_openai_api_key="test-key",
        azure_chat_deployment="chat",
        azure_embedding_deployment="embedding",
        azure_openai_api_version="2024-10-21",
        execution_backend=ExecutionBackend.DISABLED,
        execution_timeout_seconds=5,
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
        retrieval_limit=5,
        retrieval_min_score=0.25,
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
