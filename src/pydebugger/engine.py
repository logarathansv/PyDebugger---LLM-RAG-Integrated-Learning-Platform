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
    HintProgress,
    TutorResponse,
)
from .providers import (
    AzureOpenAIChatProvider,
    AzureOpenAIEmbeddingProvider,
    ChatProvider,
    EmbeddingProvider,
)
from .retrieval import ChromaVectorIndex, VectorIndex
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
        vector_index: VectorIndex | None = None,
    ) -> None:
        chat_provider = chat_provider or AzureOpenAIChatProvider(settings)
        embedding_provider = embedding_provider or AzureOpenAIEmbeddingProvider(settings)
        self.settings = settings
        self.ingestor = DocumentIngestor()
        self.index = vector_index or ChromaVectorIndex(
            embedding_provider,
            settings.vector_dir,
            settings.vector_collection,
        )
        self.tutor = TutorService(
            chat_provider,
            self.index,
            settings.retrieval_limit,
            settings.retrieval_min_score,
        )
        self.executor = executor or self._build_executor(settings)
        self.executor.validate_runtime()
        self.execution = ExecutionService(self.executor)
        self.transcript_provider = transcript_provider
        self._documents: dict[str, DocumentSource] = {}
        self._lock = RLock()
        (settings.data_dir / "uploads").mkdir(parents=True, exist_ok=True)
        self._restore_document_catalog()

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
        source_key = f"upload:{safe_name.casefold()}"
        document_id = self._stable_id("upload", source_key)
        content_hash = hashlib.sha256(data).hexdigest()
        index_version = self._index_version(content_hash)
        with self._lock:
            current = self._documents.get(document_id)
        if current and current.index_version == index_version:
            return current
        upload_dir = self.settings.data_dir / "uploads" / document_id
        upload_dir.mkdir(parents=True, exist_ok=True)
        path = upload_dir / f"{content_hash[:16]}-{safe_name}"
        try:
            path.write_bytes(data)
        except OSError as error:
            raise IngestionError("The uploaded document could not be stored") from error
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
            chunks = self.ingestor.ingest(source)
            self.index.replace_document(document_id, chunks)
        except Exception:
            path.unlink(missing_ok=True)
            self._remove_empty_directory(upload_dir)
            raise
        with self._lock:
            self._documents[source.id] = source
        if current and current.path != path:
            current.path.unlink(missing_ok=True)
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

        source_key = f"youtube:{video_id}"
        document_id = self._stable_id("youtube", source_key)
        content_hash = hashlib.sha256(transcript_data).hexdigest()
        index_version = self._index_version(content_hash)
        with self._lock:
            current = self._documents.get(document_id)
        if current and current.index_version == index_version:
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
            chunks = self.ingestor.ingest_youtube(source, transcript)
            self.index.replace_document(document_id, chunks)
        except Exception:
            path.unlink(missing_ok=True)
            self._remove_empty_directory(upload_dir)
            raise
        with self._lock:
            self._documents[source.id] = source
        if current and current.path != path:
            current.path.unlink(missing_ok=True)
        return source

    def list_documents(self) -> tuple[DocumentSource, ...]:
        with self._lock:
            return tuple(self._documents.values())

    def remove_document(self, document_id: str) -> bool:
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
        known = {document.id for document in self.list_documents()}
        selected = document_ids & known
        if not selected:
            raise ValueError("Select at least one indexed document")
        return self.tutor.answer(query, mode, selected, history)

    def next_hint(
        self,
        problem: str,
        history: list[ChatMessage] | None = None,
        previous_hints: list[str] | None = None,
    ) -> HintProgress:
        return self.tutor.next_hint(problem, history or [], previous_hints or [])

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

    def _restore_document_catalog(self) -> None:
        for metadata in self.index.list_source_metadata():
            source_id = str(metadata.get("source_id") or "")
            stored_filename = str(metadata.get("stored_filename") or "")
            source_name = str(metadata.get("source_name") or metadata.get("source") or "")
            if not source_id or not stored_filename or not source_name:
                continue
            path = self.settings.data_dir / "uploads" / source_id / stored_filename
            if not path.is_file():
                continue
            self._documents[source_id] = DocumentSource(
                id=source_id,
                name=source_name,
                path=path,
                media_type=str(metadata.get("media_type") or "application/octet-stream"),
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

