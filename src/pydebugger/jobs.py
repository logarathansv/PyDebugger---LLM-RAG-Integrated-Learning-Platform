"""Bounded background processing with observable, failure-safe job states."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Event, Lock, BoundedSemaphore
import time
from typing import Callable
import uuid

from .models import DocumentSource, IngestionJob, JobState


class JobQueueFullError(RuntimeError):
    """Raised when bounded background capacity has been exhausted."""


class JobCancelledError(RuntimeError):
    """Raised cooperatively before an ingestion operation commits."""


class JobTimeoutError(TimeoutError):
    """Raised when an ingestion deadline is reached before commit."""


ProgressReporter = Callable[[float, str], None]
IngestionHandler = Callable[
    [str, bytes, str | None, ProgressReporter, float, Event], DocumentSource
]


@dataclass
class _MutableIngestionJob:
    state: JobState
    max_attempts: int
    cancel_event: Event
    result: DocumentSource | None = None
    error: str | None = None
    error_code: str | None = None
    progress: float = 0.0
    message: str = "Queued"
    attempt: int = 0


class IngestionJobService:
    """Run ingestion in a bounded pool with cooperative timeout and retries."""

    def __init__(
        self,
        handler: IngestionHandler,
        *,
        workers: int = 2,
        queue_size: int = 8,
        timeout_seconds: float = 120.0,
        max_attempts: int = 3,
        retryable: Callable[[Exception], bool] | None = None,
    ) -> None:
        self._handler = handler
        self._timeout_seconds = timeout_seconds
        self._max_attempts = max_attempts
        self._retryable = retryable or (lambda error: isinstance(error, OSError))
        self._executor = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="pydebugger-ingestion"
        )
        self._capacity = BoundedSemaphore(workers + queue_size)
        self._jobs: dict[str, _MutableIngestionJob] = {}
        self._lock = Lock()

    def submit(
        self, filename: str, data: bytes, declared_media_type: str | None = None
    ) -> IngestionJob:
        if not self._capacity.acquire(blocking=False):
            raise JobQueueFullError(
                "The ingestion queue is full. Wait for an active upload to finish and retry."
            )
        job_id = uuid.uuid4().hex
        with self._lock:
            self._jobs[job_id] = _MutableIngestionJob(
                JobState.QUEUED, self._max_attempts, Event()
            )
        try:
            self._executor.submit(
                self._run, job_id, filename, data, declared_media_type
            )
        except Exception:
            self._capacity.release()
            with self._lock:
                self._jobs.pop(job_id, None)
            raise
        return self.get(job_id)

    def get(self, job_id: str) -> IngestionJob:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(f"Unknown ingestion job: {job_id}")
            return IngestionJob(
                id=job_id,
                state=job.state,
                result=job.result,
                error=job.error,
                error_code=job.error_code,
                progress=job.progress,
                message=job.message,
                attempt=job.attempt,
                max_attempts=job.max_attempts,
            )

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.state not in {
                JobState.QUEUED,
                JobState.RUNNING,
                JobState.RETRYING,
            }:
                return False
            job.cancel_event.set()
            if job.state is JobState.QUEUED:
                job.state = JobState.CANCELLED
                job.message = "Cancelled before ingestion started"
                job.error_code = "cancelled"
            else:
                job.message = "Cancellation requested"
            return True

    def shutdown(self, *, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=True)

    def _run(
        self, job_id: str, filename: str, data: bytes, declared_media_type: str | None
    ) -> None:
        deadline = time.monotonic() + self._timeout_seconds
        try:
            for attempt in range(1, self._max_attempts + 1):
                with self._lock:
                    job = self._jobs[job_id]
                    if job.cancel_event.is_set():
                        job.state = JobState.CANCELLED
                        job.message = "Ingestion cancelled"
                        job.error_code = "cancelled"
                        return
                    job.state = JobState.RUNNING
                    job.attempt = attempt
                    job.message = "Starting ingestion"
                try:
                    result = self._handler(
                        filename,
                        data,
                        declared_media_type,
                        lambda progress, message: self._report(job_id, progress, message),
                        deadline,
                        job.cancel_event,
                    )
                    with self._lock:
                        job = self._jobs[job_id]
                        job.state = JobState.COMPLETED
                        job.result = result
                        job.progress = 1.0
                        job.message = "Document indexed"
                        job.error = None
                        job.error_code = None
                    return
                except JobCancelledError:
                    self._finish_failure(
                        job_id, JobState.CANCELLED, "cancelled", "Ingestion cancelled"
                    )
                    return
                except JobTimeoutError as error:
                    self._finish_failure(
                        job_id, JobState.TIMED_OUT, "ingestion_timeout", str(error)
                    )
                    return
                except Exception as error:
                    can_retry = (
                        attempt < self._max_attempts
                        and time.monotonic() < deadline
                        and self._retryable(error)
                    )
                    if not can_retry:
                        self._finish_failure(
                            job_id,
                            JobState.FAILED,
                            self._error_code(error),
                            self._useful_error(error),
                        )
                        return
                    with self._lock:
                        job = self._jobs[job_id]
                        job.state = JobState.RETRYING
                        job.message = (
                            f"Attempt {attempt} failed; retrying "
                            f"({attempt + 1}/{self._max_attempts})"
                        )
                    if job.cancel_event.wait(min(0.25 * attempt, 1.0)):
                        self._finish_failure(
                            job_id, JobState.CANCELLED, "cancelled", "Ingestion cancelled"
                        )
                        return
        finally:
            self._capacity.release()

    def _report(self, job_id: str, progress: float, message: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            if job.state in {JobState.RUNNING, JobState.RETRYING}:
                job.progress = max(job.progress, min(0.99, max(0.0, progress)))
                job.message = message

    def _finish_failure(
        self, job_id: str, state: JobState, code: str, message: str
    ) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.state = state
            job.error_code = code
            job.error = message
            job.message = message

    @staticmethod
    def _error_code(error: Exception) -> str:
        name = type(error).__name__
        mapping = {
            "IngestionError": "invalid_document",
            "RetrievalError": "indexing_failed",
            "EmbeddingMismatchError": "embedding_mismatch",
        }
        return mapping.get(name, "ingestion_failed")

    @staticmethod
    def _useful_error(error: Exception) -> str:
        message = str(error).strip()
        return message if message else f"Ingestion failed ({type(error).__name__})"
