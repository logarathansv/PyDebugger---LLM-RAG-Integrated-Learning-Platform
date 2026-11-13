"""Environment-backed configuration with explicit startup validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import os
from pathlib import Path
import re
from urllib.parse import urlparse


class ConfigurationError(ValueError):
    """Raised when startup configuration is missing or unsafe."""


class ExecutionBackend(str, Enum):
    DISABLED = "disabled"
    DOCKER = "docker"


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as error:
        raise ConfigurationError(f"{name} must be an integer") from error


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError as error:
        raise ConfigurationError(f"{name} must be a number") from error


def _env_backend() -> ExecutionBackend:
    value = os.getenv("PYDEBUGGER_EXECUTION_BACKEND", "disabled").strip().lower()
    try:
        return ExecutionBackend(value)
    except ValueError as error:
        choices = ", ".join(item.value for item in ExecutionBackend)
        raise ConfigurationError(
            f"PYDEBUGGER_EXECUTION_BACKEND must be one of: {choices}"
        ) from error


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    vector_dir: Path
    vector_collection: str
    azure_openai_endpoint: str | None
    azure_openai_api_key: str | None = field(repr=False)
    azure_chat_deployment: str | None
    azure_embedding_deployment: str | None
    azure_openai_api_version: str
    execution_backend: ExecutionBackend
    execution_timeout_seconds: float
    execution_output_limit: int
    execution_source_limit: int
    execution_memory_mb: int
    execution_cpus: float
    execution_pids_limit: int
    execution_tmpfs_mb: int
    docker_binary: str
    docker_image: str
    docker_runtime: str | None
    upload_limit_bytes: int
    retrieval_limit: int
    retrieval_min_score: float

    @classmethod
    def from_env(
        cls,
        root: Path | None = None,
        *,
        require_ai: bool = True,
    ) -> "Settings":
        project_root = (root or Path.cwd()).resolve()
        data_dir = Path(
            os.getenv("PYDEBUGGER_DATA_DIR", str(project_root / ".pydebugger-data"))
        )
        if not data_dir.is_absolute():
            data_dir = project_root / data_dir
        vector_dir = Path(
            os.getenv("PYDEBUGGER_VECTOR_DIR", str(data_dir / "chroma"))
        )
        if not vector_dir.is_absolute():
            vector_dir = project_root / vector_dir
        settings = cls(
            data_dir=data_dir.resolve(),
            vector_dir=vector_dir.resolve(),
            vector_collection=os.getenv(
                "PYDEBUGGER_VECTOR_COLLECTION", "pydebugger-course-content"
            ).strip(),
            azure_openai_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
            azure_openai_api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            azure_chat_deployment=os.getenv("AZURE_CHAT_DEPLOYMENT"),
            azure_embedding_deployment=os.getenv("AZURE_EMBEDDING_DEPLOYMENT"),
            azure_openai_api_version=os.getenv(
                "AZURE_OPENAI_API_VERSION", "2024-10-21"
            ),
            execution_backend=_env_backend(),
            execution_timeout_seconds=_env_float(
                "PYDEBUGGER_EXECUTION_TIMEOUT_SECONDS", 5.0
            ),
            execution_output_limit=_env_int(
                "PYDEBUGGER_EXECUTION_OUTPUT_LIMIT", 20_000
            ),
            execution_source_limit=_env_int(
                "PYDEBUGGER_EXECUTION_SOURCE_LIMIT", 100_000
            ),
            execution_memory_mb=_env_int("PYDEBUGGER_EXECUTION_MEMORY_MB", 128),
            execution_cpus=_env_float("PYDEBUGGER_EXECUTION_CPUS", 0.5),
            execution_pids_limit=_env_int("PYDEBUGGER_EXECUTION_PIDS_LIMIT", 32),
            execution_tmpfs_mb=_env_int("PYDEBUGGER_EXECUTION_TMPFS_MB", 16),
            docker_binary=os.getenv("PYDEBUGGER_DOCKER_BINARY", "docker"),
            docker_image=os.getenv(
                "PYDEBUGGER_DOCKER_IMAGE",
                "python:3.13-slim-bookworm@sha256:"
                "a5a7017efc5969e00ae2b91c8eb0631049a569652aa4ffc4501769049ae45c72",
            ),
            docker_runtime=os.getenv("PYDEBUGGER_DOCKER_RUNTIME") or None,
            upload_limit_bytes=_env_int(
                "PYDEBUGGER_UPLOAD_LIMIT_BYTES", 10 * 1024 * 1024
            ),
            retrieval_limit=_env_int("PYDEBUGGER_RETRIEVAL_LIMIT", 5),
            retrieval_min_score=_env_float("PYDEBUGGER_RETRIEVAL_MIN_SCORE", 0.25),
        )
        settings.validate(require_ai=require_ai)
        return settings

    def validate(self, *, require_ai: bool = True) -> None:
        errors: list[str] = []
        if require_ai:
            errors.extend(self._validate_ai())
        if self.data_dir.exists() and not self.data_dir.is_dir():
            errors.append("PYDEBUGGER_DATA_DIR must point to a directory")
        if self.vector_dir.exists() and not self.vector_dir.is_dir():
            errors.append("PYDEBUGGER_VECTOR_DIR must point to a directory")
        if not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]{1,61}[A-Za-z0-9]",
            self.vector_collection,
        ):
            errors.append(
                "PYDEBUGGER_VECTOR_COLLECTION must be 3-63 letters, numbers, dots, "
                "underscores, or hyphens and start/end with a letter or number"
            )
        self._check_range(
            errors, "PYDEBUGGER_UPLOAD_LIMIT_BYTES", self.upload_limit_bytes, 1, 100_000_000
        )
        self._check_range(errors, "PYDEBUGGER_RETRIEVAL_LIMIT", self.retrieval_limit, 1, 20)
        self._check_range(
            errors, "PYDEBUGGER_RETRIEVAL_MIN_SCORE", self.retrieval_min_score, 0.0, 1.0
        )
        self._check_range(
            errors,
            "PYDEBUGGER_EXECUTION_TIMEOUT_SECONDS",
            self.execution_timeout_seconds,
            0.1,
            30.0,
        )
        self._check_range(
            errors,
            "PYDEBUGGER_EXECUTION_OUTPUT_LIMIT",
            self.execution_output_limit,
            1_024,
            1_000_000,
        )
        self._check_range(
            errors,
            "PYDEBUGGER_EXECUTION_SOURCE_LIMIT",
            self.execution_source_limit,
            1_024,
            1_000_000,
        )
        self._check_range(
            errors, "PYDEBUGGER_EXECUTION_MEMORY_MB", self.execution_memory_mb, 32, 512
        )
        self._check_range(
            errors, "PYDEBUGGER_EXECUTION_CPUS", self.execution_cpus, 0.1, 2.0
        )
        self._check_range(
            errors, "PYDEBUGGER_EXECUTION_PIDS_LIMIT", self.execution_pids_limit, 8, 128
        )
        self._check_range(
            errors, "PYDEBUGGER_EXECUTION_TMPFS_MB", self.execution_tmpfs_mb, 4, 64
        )
        if self.execution_backend is ExecutionBackend.DOCKER:
            if "@sha256:" not in self.docker_image:
                errors.append(
                    "PYDEBUGGER_DOCKER_IMAGE must be pinned to an immutable sha256 digest"
                )
            if not self.docker_binary.strip():
                errors.append("PYDEBUGGER_DOCKER_BINARY cannot be empty")
        if errors:
            raise ConfigurationError("Invalid configuration:\n- " + "\n- ".join(errors))

    def _validate_ai(self) -> list[str]:
        errors: list[str] = []
        required = (
            ("AZURE_OPENAI_ENDPOINT", self.azure_openai_endpoint),
            ("AZURE_OPENAI_API_KEY", self.azure_openai_api_key),
            ("AZURE_CHAT_DEPLOYMENT", self.azure_chat_deployment),
            ("AZURE_EMBEDDING_DEPLOYMENT", self.azure_embedding_deployment),
        )
        for name, value in required:
            if not value or value.strip().startswith("<"):
                errors.append(f"{name} is required and cannot be a placeholder")
        if self.azure_openai_endpoint:
            endpoint = urlparse(self.azure_openai_endpoint)
            if endpoint.scheme != "https" or not endpoint.hostname:
                errors.append("AZURE_OPENAI_ENDPOINT must be a valid HTTPS base URL")
            if "/openai/deployments/" in endpoint.path:
                errors.append(
                    "AZURE_OPENAI_ENDPOINT must be the resource base URL, not a deployment URL"
                )
        return errors

    @staticmethod
    def _check_range(
        errors: list[str], name: str, value: float, minimum: float, maximum: float
    ) -> None:
        if not minimum <= value <= maximum:
            errors.append(f"{name} must be between {minimum:g} and {maximum:g}")
