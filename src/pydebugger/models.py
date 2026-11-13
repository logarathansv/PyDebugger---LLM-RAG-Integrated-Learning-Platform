"""Small data contracts shared by backend modules."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class AssistantMode(str, Enum):
    PROGRAMMING_TUTOR = "Programming Tutor"
    RUBBER_DUCK = "Rubber Duck Assistant"


class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRYING = "retrying"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


# Compatibility name retained for callers of the execution API.
ExecutionState = JobState


@dataclass(frozen=True)
class DocumentSource:
    id: str
    name: str
    path: Path
    media_type: str
    sha256: str
    index_version: str = ""
    source_key: str | None = None
    source_type: str = "upload"
    origin_uri: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DocumentChunk:
    id: str
    document_id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RetrievedChunk:
    chunk: DocumentChunk
    score: float


@dataclass(frozen=True)
class Citation:
    document_id: str
    source_name: str
    excerpt: str
    location: str | None = None
    chunk_id: str | None = None
    score: float | None = None
    source_uri: str | None = None


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str


@dataclass(frozen=True)
class TutorResponse:
    answer: str
    citations: tuple[Citation, ...] = ()
    grounded: bool = True


@dataclass(frozen=True)
class HintProgress:
    problem: str
    level: int
    hint: str
    hints: tuple[str, ...]


@dataclass(frozen=True)
class TracebackFrame:
    file_name: str
    line_number: int
    function_name: str
    source_line: str | None = None


@dataclass(frozen=True)
class PythonDiagnostic:
    exception_type: str
    message: str
    line_number: int | None = None
    column_number: int | None = None
    end_line_number: int | None = None
    end_column_number: int | None = None
    source_line: str | None = None
    frames: tuple[TracebackFrame, ...] = ()


@dataclass(frozen=True)
class ExecutionResult:
    execution_id: str | None = None
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    duration_seconds: float = 0.0
    timed_out: bool = False
    cancelled: bool = False
    resource_limit_exceeded: bool = False
    truncated: bool = False
    disabled_reason: str | None = None
    diagnostic: PythonDiagnostic | None = None
    traceback: str = ""


@dataclass(frozen=True)
class ExecutionJob:
    id: str
    state: ExecutionState
    result: ExecutionResult | None = None
    error: str | None = None
    error_code: str | None = None
    progress: float = 0.0
    message: str = "Queued"
    attempt: int = 0
    max_attempts: int = 1


@dataclass(frozen=True)
class IngestionJob:
    id: str
    state: JobState
    result: DocumentSource | None = None
    error: str | None = None
    error_code: str | None = None
    progress: float = 0.0
    message: str = "Queued"
    attempt: int = 0
    max_attempts: int = 1

