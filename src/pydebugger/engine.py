"""Single composition root for the supported PyDebugger backend."""

from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
import re
from threading import RLock
import uuid

from .config import Settings
from .execution import CodeExecutor, DisabledExecutor, LocalSubprocessExecutor
from .ingestion import DocumentIngestor, IngestionError
from .models import AssistantMode, ChatMessage, DocumentSource, ExecutionResult, TutorResponse
from .providers import AzureOpenAIProvider, ChatProvider, EmbeddingProvider
from .retrieval import InMemoryVectorIndex
from .tutoring import TutorService


class PyDebuggerEngine:
    def __init__(
        self,
        settings: Settings,
        chat_provider: ChatProvider | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        executor: CodeExecutor | None = None,
    ) -> None:
        if chat_provider is None or embedding_provider is None:
            azure = AzureOpenAIProvider(settings)
            chat_provider = chat_provider or azure
            embedding_provider = embedding_provider or azure
        self.settings = settings
        self.ingestor = DocumentIngestor()
        self.index = InMemoryVectorIndex(embedding_provider)
        self.tutor = TutorService(chat_provider, self.index, settings.retrieval_limit)
        self.executor = executor or (
            LocalSubprocessExecutor(
                settings.execution_timeout_seconds,
                settings.execution_output_limit,
            )
            if settings.allow_local_execution
            else DisabledExecutor()
        )
        self._documents: dict[str, DocumentSource] = {}
        self._lock = RLock()
        (settings.data_dir / "uploads").mkdir(parents=True, exist_ok=True)

    def add_document(self, filename: str, data: bytes) -> DocumentSource:
        if not data:
            raise IngestionError("The uploaded document is empty")
        if len(data) > self.settings.upload_limit_bytes:
            raise IngestionError("The uploaded document exceeds the configured size limit")
        safe_name = self._safe_filename(filename)
        suffix = Path(safe_name).suffix.lower()
        if suffix not in self.ingestor.SUPPORTED_SUFFIXES:
            raise IngestionError(f"Unsupported document type: {suffix or 'unknown'}")
        document_id = uuid.uuid4().hex
        upload_dir = self.settings.data_dir / "uploads" / document_id
        upload_dir.mkdir(parents=True, exist_ok=False)
        path = upload_dir / safe_name
        path.write_bytes(data)
        source = DocumentSource(
            id=document_id,
            name=safe_name,
            path=path,
            media_type=mimetypes.guess_type(safe_name)[0] or "application/octet-stream",
            sha256=hashlib.sha256(data).hexdigest(),
        )
        try:
            chunks = self.ingestor.ingest(source)
            self.index.add(chunks)
        except Exception:
            path.unlink(missing_ok=True)
            upload_dir.rmdir()
            raise
        with self._lock:
            self._documents[source.id] = source
        return source

    def list_documents(self) -> tuple[DocumentSource, ...]:
        with self._lock:
            return tuple(self._documents.values())

    def remove_document(self, document_id: str) -> bool:
        with self._lock:
            source = self._documents.pop(document_id, None)
        if source is None:
            return False
        self.index.remove_document(document_id)
        source.path.unlink(missing_ok=True)
        try:
            source.path.parent.rmdir()
        except OSError:
            pass
        return True

    def answer(
        self,
        query: str,
        mode: AssistantMode,
        document_ids: set[str],
        history: list[ChatMessage] | None = None,
    ) -> TutorResponse:
        known = {document.id for document in self.list_documents()}
        selected = document_ids & known
        if not selected:
            raise ValueError("Select at least one indexed document")
        return self.tutor.answer(query, mode, selected, history)

    def execute(self, source: str) -> ExecutionResult:
        return self.executor.execute(source)

    @staticmethod
    def _safe_filename(filename: str) -> str:
        name = Path(filename).name.strip()
        name = re.sub(r"[^A-Za-z0-9._ -]", "_", name)
        if not name or name in {".", ".."}:
            raise IngestionError("The uploaded document needs a valid filename")
        return name[:180]

