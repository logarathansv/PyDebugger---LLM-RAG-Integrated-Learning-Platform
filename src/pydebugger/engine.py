"""Single composition root for the supported PyDebugger backend."""

from __future__ import annotations

import hashlib
import re
import time
import uuid
from collections.abc import Callable, Iterable
from pathlib import Path
from threading import Event, RLock
from typing import TypeVar

from .config import AIProvider, ExecutionBackend, Settings
from .execution import (
    CodeExecutor,
    DisabledExecutor,
    DockerCodeExecutor,
    DockerExecutionPolicy,
    ExecutionService,
)
from .ingestion import DocumentIngestor, IngestionError
from .jobs import IngestionJobService, JobCancelledError, JobTimeoutError
from .models import (
    AssistantMode,
    ChatMessage,
    DocumentChunk,
    DocumentSource,
    ExecutionJob,
    ExecutionProject,
    ExecutionResult,
    ExecutionTestCase,
    HintProgress,
    IngestionJob,
    ProjectFile,
    RetrievedChunk,
    TutorResponse,
)
from .monitoring import (
    MetricSummary,
    MonitoredChatProvider,
    MonitoredEmbeddingProvider,
    MonitoringStore,
    OperationMetric,
)
from .providers import (
    AzureOpenAIChatProvider,
    AzureOpenAIEmbeddingProvider,
    ChatProvider,
    EmbeddingProvider,
    GoogleGeminiChatProvider,
    GoogleGeminiEmbeddingProvider,
)
from .retrieval import (
    ChromaVectorIndex,
    EmbeddingMismatchError,
    RetrievalError,
    VectorIndex,
)
from .tutoring import TutorService
from .youtube import (
    TranscriptError,
    TranscriptProvider,
    YouTubeTranscriptProvider,
    canonical_youtube_url,
    parse_youtube_video_id,
)


class DocumentProcessor(DocumentIngestor):
    """Load validated documents and split them into traceable text chunks."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        super().__init__()

    def load(self, source: DocumentSource) -> list[DocumentChunk]:
        return self.ingest(source)


class VectorStoreManager:
    """Own the embedding client and vector database used by RAG."""

    def __init__(
        self,
        settings: Settings,
        embeddings: EmbeddingProvider,
        vector_index: VectorIndex | None = None,
    ) -> None:
        self.settings = settings
        self.embeddings = embeddings
        self.db = vector_index or ChromaVectorIndex(
            embeddings,
            settings.vector_dir,
            settings.vector_collection,
        )

    def add(self, chunks: Iterable[DocumentChunk]) -> None:
        self.db.add(chunks)

    def replace_document(
        self, document_id: str, chunks: Iterable[DocumentChunk]
    ) -> None:
        self.db.replace_document(document_id, chunks)

    def remove_document(self, document_id: str) -> None:
        self.db.remove_document(document_id)

    def search(
        self,
        query: str,
        document_ids: set[str] | None = None,
        limit: int = 3,
    ) -> list[RetrievedChunk]:
        return self.db.search(query, document_ids, limit)

    def list_source_metadata(self) -> tuple[dict[str, object], ...]:
        return self.db.list_source_metadata()


class LLMDebugger:
    """Own the configured chat client and grounded tutoring prompts."""

    def __init__(
        self,
        settings: Settings,
        chat_client: ChatProvider,
        vector_store: VectorStoreManager,
    ) -> None:
        self.settings = settings
        self.client = chat_client
        self.vector_store = vector_store
        self.tutor = TutorService(
            chat_client,
            vector_store,
            settings.retrieval_limit,
            settings.retrieval_min_score,
            settings.retrieval_context_tokens,
            settings.conversation_context_tokens,
        )

    def execute_prompt(self, system_prompt: str, user_prompt: str) -> str:
        return self.client.complete(system_prompt, user_prompt)

    def answer(
        self,
        query: str,
        mode: AssistantMode,
        document_ids: set[str],
        history: list[ChatMessage] | None = None,
    ) -> TutorResponse:
        return self.tutor.answer(query, mode, document_ids, history)

    def next_hint(
        self,
        problem: str,
        history: list[ChatMessage],
        previous_hints: list[str],
    ) -> HintProgress:
        return self.tutor.next_hint(problem, history, previous_hints)


class PyDebuggerApp:
    """Composition root linking documents, vectors, LLMs, jobs, and execution."""

    _YOUTUBE_JOB_MEDIA_TYPE = "application/x-pydebugger-youtube-reference"

    def __init__(
        self,
        settings: Settings,
        chat_provider: ChatProvider | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        executor: CodeExecutor | None = None,
        transcript_provider: TranscriptProvider | None = None,
        vector_index: VectorIndex | None = None,
    ) -> None:
        self.settings = settings
        self.monitor = MonitoringStore(
            settings.monitoring_db_path,
            chat_input_cost_per_million=settings.chat_input_cost_per_million,
            chat_output_cost_per_million=settings.chat_output_cost_per_million,
            embedding_cost_per_million=settings.embedding_cost_per_million,
        )
        default_chat, default_embeddings = self._build_ai_providers(
            settings, chat_provider is None, embedding_provider is None
        )
        raw_chat = chat_provider or default_chat
        raw_embeddings = embedding_provider or default_embeddings
        if raw_chat is None or raw_embeddings is None:
            raise RuntimeError("Chat and embedding providers must be configured")
        chat_provider = MonitoredChatProvider(raw_chat, self.monitor)
        embedding_provider = MonitoredEmbeddingProvider(raw_embeddings, self.monitor)
        self.document_processor = DocumentProcessor(settings)
        self.ingestor = self.document_processor
        self.vector_store = VectorStoreManager(
            settings,
            embedding_provider,
            vector_index,
        )
        self.index = self.vector_store
        self.llm_debugger = LLMDebugger(
            settings,
            chat_provider,
            self.vector_store,
        )
        self.tutor = self.llm_debugger
        self.executor = executor or self._build_executor(settings)
        self.executor.validate_runtime()
        self.execution = ExecutionService(
            self.executor,
            workers=settings.background_workers,
            queue_size=settings.background_queue_size,
            max_attempts=settings.execution_max_attempts,
            monitor=self.monitor,
        )
        self.transcript_provider = transcript_provider
        self._documents: dict[str, DocumentSource] = {}
        self._lock = RLock()
        self._source_locks: dict[str, RLock] = {}
        (settings.data_dir / "uploads").mkdir(parents=True, exist_ok=True)
        self._restore_document_catalog()
        self.ingestion_jobs = IngestionJobService(
            self._run_ingestion_job,
            workers=settings.background_workers,
            queue_size=settings.background_queue_size,
            timeout_seconds=settings.ingestion_timeout_seconds,
            max_attempts=settings.ingestion_max_attempts,
            retryable=self._is_retryable_ingestion_error,
        )

    def add_document(
        self,
        filename: str,
        data: bytes,
        declared_media_type: str | None = None,
        **control: object,
    ) -> DocumentSource:
        safe_name = self._safe_filename(filename)
        source_key = f"upload:{safe_name.casefold()}"
        with self._source_operation_lock(source_key):
            return self._observe(
                "ingestion.document",
                lambda: self._add_document_locked(
                    filename, data, declared_media_type, **control
                ),
                {"media_type": declared_media_type or "unknown"},
            )

    def _add_document_locked(
        self,
        filename: str,
        data: bytes,
        declared_media_type: str | None = None,
        *,
        _report: Callable[[float, str], None] | None = None,
        _deadline: float | None = None,
        _cancel_event: Event | None = None,
    ) -> DocumentSource:
        report = _report or (lambda progress, message: None)
        self._check_ingestion_control(_deadline, _cancel_event)
        report(0.05, "Validating upload")
        if not data:
            raise IngestionError("The uploaded document is empty")
        if len(data) > self.settings.upload_limit_bytes:
            raise IngestionError(
                "The uploaded document exceeds the configured size limit"
            )
        safe_name = self._safe_filename(filename)
        media_type = self.ingestor.validate_upload(safe_name, data, declared_media_type)
        self._check_ingestion_control(_deadline, _cancel_event)
        report(0.15, "Preparing document revision")
        source_key = f"upload:{safe_name.casefold()}"
        document_id = self._stable_id("upload", source_key)
        content_hash = hashlib.sha256(data).hexdigest()
        index_version = self._index_version(content_hash)
        with self._lock:
            current = self._documents.get(document_id)
        if current and current.index_version == index_version:
            report(1.0, "Document is already indexed")
            return current
        upload_dir = self.settings.data_dir / "uploads" / document_id
        upload_dir.mkdir(parents=True, exist_ok=True)
        path = upload_dir / f"{content_hash[:16]}-{safe_name}"
        try:
            path.write_bytes(data)
        except OSError as error:
            raise IngestionError("The uploaded document could not be stored") from error
        report(0.3, "Document stored; extracting text")
        source = DocumentSource(
            id=document_id,
            name=safe_name,
            path=path,
            media_type=media_type,
            sha256=content_hash,
            index_version=index_version,
            source_key=source_key,
            metadata={
                "original_filename": filename,
                "stored_filename": path.name,
            },
        )
        try:
            self._check_ingestion_control(_deadline, _cancel_event)
            chunks = self.ingestor.ingest(source)
            self._check_ingestion_control(_deadline, _cancel_event)
            report(0.65, f"Extracted {len(chunks)} chunks; creating embeddings")
            self.index.replace_document(document_id, chunks)
        except Exception:
            path.unlink(missing_ok=True)
            self._remove_empty_directory(upload_dir)
            raise
        with self._lock:
            self._documents[source.id] = source
        report(0.95, "Finalizing document revision")
        if current and current.path != path:
            current.path.unlink(missing_ok=True)
        return source

    def start_document_ingestion(
        self,
        filename: str,
        data: bytes,
        declared_media_type: str | None = None,
    ) -> IngestionJob:
        return self.ingestion_jobs.submit(filename, data, declared_media_type)

    def get_ingestion(self, job_id: str) -> IngestionJob:
        return self.ingestion_jobs.get(job_id)

    def cancel_ingestion(self, job_id: str) -> bool:
        return self.ingestion_jobs.cancel(job_id)

    def add_youtube_transcript(
        self,
        reference: str,
        languages: tuple[str, ...] = ("en",),
        **control: object,
    ) -> DocumentSource:
        video_id = parse_youtube_video_id(reference)
        with self._source_operation_lock(f"youtube:{video_id}"):
            return self._observe(
                "ingestion.youtube",
                lambda: self._add_youtube_transcript_locked(
                    reference, languages, **control
                ),
                {"language_count": len(languages)},
            )

    def _add_youtube_transcript_locked(
        self,
        reference: str,
        languages: tuple[str, ...] = ("en",),
        *,
        _report: Callable[[float, str], None] | None = None,
        _deadline: float | None = None,
        _cancel_event: Event | None = None,
    ) -> DocumentSource:
        report = _report or (lambda progress, message: None)
        self._check_ingestion_control(_deadline, _cancel_event)
        report(0.05, "Validating YouTube reference")
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
        report(0.15, "Fetching transcript")
        try:
            transcript = provider.fetch(video_id, normalized_languages)
        except TranscriptError as error:
            raise IngestionError(str(error)) from error
        self._check_ingestion_control(_deadline, _cancel_event)
        transcript_text = "\n".join(segment.text for segment in transcript.segments)
        transcript_data = transcript_text.encode("utf-8")
        if not transcript_data:
            raise IngestionError("The video transcript is empty")
        if len(transcript_data) > self.settings.upload_limit_bytes:
            raise IngestionError(
                "The video transcript exceeds the configured size limit"
            )

        source_key = f"youtube:{video_id}"
        document_id = self._stable_id("youtube", source_key)
        content_hash = hashlib.sha256(transcript_data).hexdigest()
        index_version = self._index_version(content_hash)
        with self._lock:
            current = self._documents.get(document_id)
        if current and current.index_version == index_version:
            report(1.0, "Transcript is already indexed")
            return current
        upload_dir = self.settings.data_dir / "uploads" / document_id
        upload_dir.mkdir(parents=True, exist_ok=True)
        filename = f"youtube-{video_id}.txt"
        stored_filename = f"{content_hash[:16]}-{filename}"
        path = upload_dir / stored_filename
        try:
            path.write_bytes(transcript_data)
        except OSError as error:
            raise IngestionError("The video transcript could not be stored") from error
        report(0.35, "Transcript stored; preparing timestamped chunks")
        source = DocumentSource(
            id=document_id,
            name=f"YouTube {video_id}",
            path=path,
            media_type="text/vnd.youtube-transcript",
            sha256=content_hash,
            index_version=index_version,
            source_key=source_key,
            source_type="youtube",
            origin_uri=canonical_youtube_url(video_id),
            metadata={
                "video_id": video_id,
                "language": transcript.language,
                "language_code": transcript.language_code,
                "is_generated": transcript.is_generated,
                "stored_filename": stored_filename,
            },
        )
        try:
            self._check_ingestion_control(_deadline, _cancel_event)
            chunks = self.ingestor.ingest_youtube(source, transcript)
            self._check_ingestion_control(_deadline, _cancel_event)
            report(0.65, f"Prepared {len(chunks)} chunks; creating embeddings")
            self.index.replace_document(document_id, chunks)
        except Exception:
            path.unlink(missing_ok=True)
            self._remove_empty_directory(upload_dir)
            raise
        with self._lock:
            self._documents[source.id] = source
        report(0.95, "Finalizing transcript revision")
        if current and current.path != path:
            current.path.unlink(missing_ok=True)
        return source

    def start_youtube_ingestion(
        self,
        reference: str,
        languages: tuple[str, ...] = ("en",),
    ) -> IngestionJob:
        encoded_languages = "\n".join(languages).encode("utf-8")
        return self.ingestion_jobs.submit(
            reference, encoded_languages, self._YOUTUBE_JOB_MEDIA_TYPE
        )

    def list_documents(self) -> tuple[DocumentSource, ...]:
        with self._lock:
            return tuple(self._documents.values())

    def remove_document(self, document_id: str) -> bool:
        with self._lock:
            source = self._documents.get(document_id)
        if source is None:
            return False
        with self._source_operation_lock(source.source_key or source.id):
            return self._remove_document_locked(document_id)

    def _remove_document_locked(self, document_id: str) -> bool:
        with self._lock:
            source = self._documents.get(document_id)
        if source is None:
            return False
        self.index.remove_document(document_id)
        with self._lock:
            self._documents.pop(document_id, None)
        document_dir = (self.settings.data_dir / "uploads" / document_id).resolve()
        uploads_dir = (self.settings.data_dir / "uploads").resolve()
        if document_dir.parent == uploads_dir and document_dir.is_dir():
            for child in document_dir.iterdir():
                if child.is_file():
                    child.unlink(missing_ok=True)
            self._remove_empty_directory(document_dir)
        return True

    def answer(
        self,
        query: str,
        mode: AssistantMode,
        document_ids: set[str],
        history: list[ChatMessage] | None = None,
    ) -> TutorResponse:
        def answer_selected() -> TutorResponse:
            known = {document.id for document in self.list_documents()}
            selected = document_ids & known
            if not selected:
                raise ValueError("Select at least one indexed document")
            return self.tutor.answer(query, mode, selected, history)

        return self._observe(
            "tutor.answer",
            answer_selected,
            {
                "selected_documents": len(document_ids),
                "history_messages": len(history or []),
            },
        )

    def monitoring_summary(
        self, *, since_seconds: float | None = 86_400
    ) -> tuple[MetricSummary, ...]:
        return self.monitor.summaries(since_seconds=since_seconds)

    def next_hint(
        self,
        problem: str,
        history: list[ChatMessage] | None = None,
        previous_hints: list[str] | None = None,
    ) -> HintProgress:
        return self._observe(
            "tutor.hint",
            lambda: self.tutor.next_hint(problem, history or [], previous_hints or []),
            {"hint_level": len(previous_hints or []) + 1},
        )

    def execute(self, source: str) -> ExecutionResult:
        execution_id = uuid.uuid4().hex
        return self._observe(
            "execution.sync",
            lambda: self.executor.execute(source, execution_id),
            {"source_characters": len(source)},
        )

    def start_execution(self, source: str) -> ExecutionJob:
        return self.execution.submit(source)

    def start_project_execution(
        self,
        files: dict[str, str],
        entrypoint: str,
        tests: list[ExecutionTestCase] | None = None,
    ) -> ExecutionJob:
        project = ExecutionProject(
            files=tuple(ProjectFile(path, source) for path, source in files.items()),
            entrypoint=entrypoint,
            tests=tuple(tests or ()),
        )
        return self.execution.submit_project(project)

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
    def _build_ai_providers(
        settings: Settings, need_chat: bool, need_embeddings: bool
    ) -> tuple[ChatProvider | None, EmbeddingProvider | None]:
        if not need_chat and not need_embeddings:
            return None, None
        if settings.ai_provider is AIProvider.GEMINI:
            return (
                GoogleGeminiChatProvider(settings) if need_chat else None,
                GoogleGeminiEmbeddingProvider(settings) if need_embeddings else None,
            )
        return (
            AzureOpenAIChatProvider(settings) if need_chat else None,
            AzureOpenAIEmbeddingProvider(settings) if need_embeddings else None,
        )

    def _restore_document_catalog(self) -> None:
        for metadata in self.index.list_source_metadata():
            source_id = str(metadata.get("source_id") or "")
            stored_filename = str(metadata.get("stored_filename") or "")
            source_name = str(
                metadata.get("source_name") or metadata.get("source") or ""
            )
            if not source_id or not stored_filename or not source_name:
                continue
            path = self.settings.data_dir / "uploads" / source_id / stored_filename
            if not path.is_file():
                continue
            self._documents[source_id] = DocumentSource(
                id=source_id,
                name=source_name,
                path=path,
                media_type=str(
                    metadata.get("media_type") or "application/octet-stream"
                ),
                sha256=str(metadata.get("sha256") or ""),
                index_version=str(metadata.get("index_version") or ""),
                source_key=str(metadata.get("source_key") or source_id),
                source_type=str(metadata.get("source_type") or "upload"),
                origin_uri=str(metadata["origin_uri"])
                if metadata.get("origin_uri")
                else None,
                metadata={
                    "stored_filename": stored_filename,
                    "original_filename": metadata.get("original_filename", source_name),
                },
            )

    def _source_operation_lock(self, source_key: str) -> RLock:
        with self._lock:
            return self._source_locks.setdefault(source_key, RLock())

    def _run_ingestion_job(
        self,
        filename: str,
        data: bytes,
        declared_media_type: str | None,
        report: Callable[[float, str], None],
        deadline: float,
        cancel_event: Event,
    ) -> DocumentSource:
        if declared_media_type == self._YOUTUBE_JOB_MEDIA_TYPE:
            try:
                languages = tuple(data.decode("utf-8").splitlines())
            except UnicodeDecodeError as error:
                raise IngestionError("Transcript language data is invalid") from error
            return self.add_youtube_transcript(
                filename,
                languages,
                _report=report,
                _deadline=deadline,
                _cancel_event=cancel_event,
            )
        return self.add_document(
            filename,
            data,
            declared_media_type,
            _report=report,
            _deadline=deadline,
            _cancel_event=cancel_event,
        )

    @staticmethod
    def _check_ingestion_control(
        deadline: float | None, cancel_event: Event | None
    ) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise JobCancelledError("Ingestion was cancelled")
        if deadline is not None and time.monotonic() >= deadline:
            raise JobTimeoutError("Document ingestion exceeded its configured deadline")

    @staticmethod
    def _is_retryable_ingestion_error(error: Exception) -> bool:
        return isinstance(error, (OSError, RetrievalError)) and not isinstance(
            error, EmbeddingMismatchError
        )

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

    @staticmethod
    def _stable_id(kind: str, source_key: str) -> str:
        digest = hashlib.sha256(source_key.encode("utf-8")).hexdigest()[:24]
        return f"{kind}-{digest}"

    @staticmethod
    def _index_version(content_hash: str) -> str:
        schema = DocumentIngestor.INDEX_SCHEMA_VERSION
        digest = hashlib.sha256(f"{schema}:{content_hash}".encode("ascii")).hexdigest()
        return f"{schema}-{digest[:16]}"

    @staticmethod
    def _remove_empty_directory(path: Path) -> None:
        try:
            path.rmdir()
        except OSError:
            pass

    def _observe(
        self,
        operation: str,
        function: Callable[[], _ObservedResult],
        metadata: dict[str, object] | None = None,
    ) -> _ObservedResult:
        started = time.perf_counter()
        try:
            result = function()
            self.monitor.record(
                OperationMetric(
                    operation,
                    "success",
                    (time.perf_counter() - started) * 1000,
                    metadata=metadata,
                )
            )
            return result
        except Exception as error:
            self.monitor.record(
                OperationMetric(
                    operation,
                    "failure",
                    (time.perf_counter() - started) * 1000,
                    error_code=type(error).__name__,
                    metadata=metadata,
                )
            )
            raise


_ObservedResult = TypeVar("_ObservedResult")

# Backward-compatible public name used by existing integrations.
PyDebuggerEngine = PyDebuggerApp
