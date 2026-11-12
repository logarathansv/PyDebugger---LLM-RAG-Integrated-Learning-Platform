"""Small data contracts shared by backend modules."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class AssistantMode(str, Enum):
    PROGRAMMING_TUTOR = "Programming Tutor"
    RUBBER_DUCK = "Rubber Duck Assistant"


@dataclass(frozen=True)
class DocumentSource:
    id: str
    name: str
    path: Path
    media_type: str
    sha256: str


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


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str


@dataclass(frozen=True)
class TutorResponse:
    answer: str
    citations: tuple[Citation, ...] = ()


@dataclass(frozen=True)
class ExecutionResult:
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    duration_seconds: float = 0.0
    timed_out: bool = False
    truncated: bool = False
    disabled_reason: str | None = None

