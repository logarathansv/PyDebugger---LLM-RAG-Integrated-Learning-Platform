"""Single composition root for the supported PyDebugger backend."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
from threading import RLock
import uuid

from .config import ExecutionBackend, Settings
from .execution import (
    CodeExecutor,
    DisabledExecutor,
    DockerCodeExecutor,
    DockerExecutionPolicy,
    ExecutionService,
)
from .ingestion import DocumentIngestor, IngestionError
from .models import (
    AssistantMode,
    ChatMessage,
    DocumentSource,
    ExecutionJob,
    ExecutionResult,
    TutorResponse,
)
from .providers import AzureOpenAIProvider, ChatProvider, EmbeddingProvider
from .retrieval import InMemoryVectorIndex
from .tutoring import TutorService
from .youtube import (
    TranscriptError,
    TranscriptProvider,
    YouTubeTranscriptProvider,
    canonical_youtube_url,
    parse_youtube_video_id,
)


class PyDebuggerEngine:
    def __init__(
        self,
        settings: Settings,
        chat_provider: ChatProvider | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        executor: CodeExecutor | None = None,
        transcript_provider: TranscriptProvider | None = None,
    ) -> None:
        if chat_provider is None or embedding_provider is None:
            azure = AzureOpenAIProvider(settings)
            chat_provider = chat_provider or azure
            embedding_provider = embedding_provider or azure
        self.settings = settings
        self.ingestor = DocumentIngestor()
        self.index = InMemoryVectorIndex(embedding_provider)
        self.tutor = TutorService(chat_provider, self.index, settings.retrieval_limit)
        self.executor = executor or self._build_executor(settings)
        self.executor.validate_runtime()
        self.execution = ExecutionService(self.executor)
        self.transcript_provider = transcript_provider
        self._documents: dict[str, DocumentSource] = {}
        self._lock = RLock()
        (settings.data_dir / "uploads").mkdir(parents=True, exist_ok=True)

    def add_document(
        self,
        filename: str,
        data: bytes,
        declared_media_type: str | None = None,
    ) -> DocumentSource:
        if not data:
            raise IngestionError("The uploaded document is empty")
        if len(data) > self.settings.upload_limit_bytes:
            raise IngestionError("The uploaded document exceeds the configured size limit")
        safe_name = self._safe_filename(filename)
        media_type = self.ingestor.validate_upload(
            safe_name, data, declared_media_type
        )
        document_id = uuid.uuid4().hex
        upload_dir = self.settings.data_dir / "uploads" / document_id
        upload_dir.mkdir(parents=True, exist_ok=False)
        path = upload_dir / safe_name
        try:
            path.write_bytes(data)
        except OSError as error:
            upload_dir.rmdir()
            raise IngestionError("The uploaded document could not be stored") from error
        source = DocumentSource(
            id=document_id,
            name=safe_name,
            path=path,
            media_type=media_type,
            sha256=hashlib.sha256(data).hexdigest(),
            metadata={"original_filename": filename},
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

    def add_youtube_transcript(
        self,
        reference: str,
        languages: tuple[str, ...] = ("en",),
    ) -> DocumentSource:
        video_id = parse_youtube_video_id(reference)
        normalized_languages = tuple(
            language.strip().lower()
            for language in languages
            if language and language.strip()
        )
        if not normalized_languages:
            raise IngestionError("Choose at least one transcript language")
        if len(normalized_languages) > 10:
            raise IngestionError("Choose no more than 10 transcript languages")
        if any(
            not re.fullmatch(r"[a-z]{2,3}(?:-[a-z0-9]{2,8})?", item)
            for item in normalized_languages
        ):
            raise IngestionError("Transcript languages must be valid language codes")

        provider = self.transcript_provider or YouTubeTranscriptProvider()
        try:
            transcript = provider.fetch(video_id, normalized_languages)
        except TranscriptError as error:
            raise IngestionError(str(error)) from error
        transcript_text = "\n".join(segment.text for segment in transcript.segments)
        transcript_data = transcript_text.encode("utf-8")
        if not transcript_data:
            raise IngestionError("The video transcript is empty")
        if len(transcript_data) > self.settings.upload_limit_bytes:
            raise IngestionError("The video transcript exceeds the configured size limit")

        document_id = uuid.uuid4().hex
        upload_dir = self.settings.data_dir / "uploads" / document_id
        upload_dir.mkdir(parents=True, exist_ok=False)
        filename = f"youtube-{video_id}.txt"
        path = upload_dir / filename
        try:
            path.write_bytes(transcript_data)
        except OSError as error:
            upload_dir.rmdir()
            raise IngestionError("The video transcript could not be stored") from error
        source = DocumentSource(
            id=document_id,
            name=f"YouTube {video_id}",
            path=path,
            media_type="text/vnd.youtube-transcript",
            sha256=hashlib.sha256(transcript_data).hexdigest(),
            source_type="youtube",
            origin_uri=canonical_youtube_url(video_id),
            metadata={
                "video_id": video_id,
                "language": transcript.language,
                "language_code": transcript.language_code,
                "is_generated": transcript.is_generated,
            },
        )
        try:
            chunks = self.ingestor.ingest_youtube(source, transcript)
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
        execution_id = uuid.uuid4().hex
        return self.executor.execute(source, execution_id)

    def start_execution(self, source: str) -> ExecutionJob:
        return self.execution.submit(source)

    def get_execution(self, execution_id: str) -> ExecutionJob:
        return self.execution.get(execution_id)

    def cancel_execution(self, execution_id: str) -> bool:
        return self.execution.cancel(execution_id)

    @staticmethod
    def _build_executor(settings: Settings) -> CodeExecutor:
        if settings.execution_backend is ExecutionBackend.DOCKER:
            return DockerCodeExecutor(DockerExecutionPolicy.from_settings(settings))
        return DisabledExecutor()

    @staticmethod
    def _safe_filename(filename: str) -> str:
        name = Path(filename).name.strip()
        name = re.sub(r"[^A-Za-z0-9._ -]", "_", name)
        name = name.rstrip(". ")
        if not name or name in {".", ".."}:
            raise IngestionError("The uploaded document needs a valid filename")
        suffix = Path(name).suffix[:16]
        stem = Path(name).stem[: max(1, 180 - len(suffix))].rstrip(". ")
        if stem.upper() in {
            "CON",
            "PRN",
            "AUX",
            "NUL",
            *(f"COM{number}" for number in range(1, 10)),
            *(f"LPT{number}" for number in range(1, 10)),
        }:
            stem = f"_{stem}"
        return f"{stem}{suffix}"

