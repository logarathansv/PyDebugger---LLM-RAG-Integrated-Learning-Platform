"""Environment-backed configuration with explicit validation."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


class ConfigurationError(ValueError):
    pass


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be a boolean value")


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    azure_openai_endpoint: str | None
    azure_openai_api_key: str | None
    azure_chat_deployment: str | None
    azure_embedding_deployment: str | None
    azure_openai_api_version: str
    allow_local_execution: bool
    execution_timeout_seconds: float
    execution_output_limit: int
    upload_limit_bytes: int
    retrieval_limit: int

    @classmethod
    def from_env(cls, root: Path | None = None) -> "Settings":
        project_root = (root or Path.cwd()).resolve()
        data_dir = Path(os.getenv("PYDEBUGGER_DATA_DIR", project_root / ".pydebugger-data"))
        if not data_dir.is_absolute():
            data_dir = project_root / data_dir
        return cls(
            data_dir=data_dir.resolve(),
            azure_openai_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
            azure_openai_api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            azure_chat_deployment=os.getenv("AZURE_CHAT_DEPLOYMENT")
            or os.getenv("AZURE_DEPLOYMENT_NAME"),
            azure_embedding_deployment=os.getenv("AZURE_EMBEDDING_DEPLOYMENT"),
            azure_openai_api_version=os.getenv(
                "AZURE_OPENAI_API_VERSION", "2024-10-21"
            ),
            allow_local_execution=_env_bool("PYDEBUGGER_ALLOW_LOCAL_EXECUTION"),
            execution_timeout_seconds=float(
                os.getenv("PYDEBUGGER_EXECUTION_TIMEOUT_SECONDS", "5")
            ),
            execution_output_limit=int(
                os.getenv("PYDEBUGGER_EXECUTION_OUTPUT_LIMIT", "20000")
            ),
            upload_limit_bytes=int(os.getenv("PYDEBUGGER_UPLOAD_LIMIT_BYTES", "10485760")),
            retrieval_limit=int(os.getenv("PYDEBUGGER_RETRIEVAL_LIMIT", "5")),
        )

    def validate_ai(self) -> None:
        missing = [
            name
            for name, value in (
                ("AZURE_OPENAI_ENDPOINT", self.azure_openai_endpoint),
                ("AZURE_OPENAI_API_KEY", self.azure_openai_api_key),
                ("AZURE_CHAT_DEPLOYMENT", self.azure_chat_deployment),
                ("AZURE_EMBEDDING_DEPLOYMENT", self.azure_embedding_deployment),
            )
            if not value
        ]
        if missing:
            raise ConfigurationError(
                "Missing required AI configuration: " + ", ".join(missing)
            )
        if self.execution_timeout_seconds <= 0:
            raise ConfigurationError("Execution timeout must be greater than zero")
        if self.execution_output_limit <= 0 or self.upload_limit_bytes <= 0:
            raise ConfigurationError("Configured size limits must be greater than zero")

