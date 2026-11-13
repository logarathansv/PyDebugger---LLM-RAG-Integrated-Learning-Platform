from __future__ import annotations

from threading import Event
import time

import pytest

from pydebugger.execution import ExecutionService
from pydebugger.engine import PyDebuggerEngine
from pydebugger.execution import DisabledExecutor
from pydebugger.jobs import (
    IngestionJobService,
    JobCancelledError,
    JobQueueFullError,
    JobTimeoutError,
)
from pydebugger.models import DocumentSource, ExecutionResult, JobState

from test_core_engine import FakeAIProvider, make_settings
from test_ingestion import FakeTranscriptProvider


def wait_for(getter, job_id: str, timeout: float = 2):
    deadline = time.monotonic() + timeout
    while True:
        job = getter(job_id)
        if job.state not in {JobState.QUEUED, JobState.RUNNING, JobState.RETRYING}:
            return job
        assert time.monotonic() < deadline
        time.sleep(0.01)


def source(tmp_path, filename="lesson.txt"):
    path = tmp_path / filename
    path.write_text("lesson", encoding="utf-8")
    return DocumentSource("doc", filename, path, "text/plain", "hash")


def test_ingestion_job_reports_progress_and_result(tmp_path):
    expected = source(tmp_path)

    def handler(filename, data, media_type, report, deadline, cancel_event):
        report(0.25, "Extracting text")
        report(0.75, "Creating embeddings")
        return expected

    service = IngestionJobService(handler, workers=1, queue_size=1)
    job = service.submit("lesson.txt", b"lesson", "text/plain")
    finished = wait_for(service.get, job.id)
    service.shutdown()

    assert finished.state is JobState.COMPLETED
    assert finished.progress == 1.0
    assert finished.result == expected
    assert finished.attempt == 1


def test_engine_background_ingestion_indexes_document(tmp_path):
    provider = FakeAIProvider()
    engine = PyDebuggerEngine(
        make_settings(tmp_path), provider, provider, DisabledExecutor()
    )
    job = engine.start_document_ingestion(
        "lesson.txt", b"Python loop lesson", "text/plain"
    )
    finished = wait_for(engine.get_ingestion, job.id)

    assert finished.state is JobState.COMPLETED
    assert finished.result in engine.list_documents()
    assert finished.message == "Document indexed"
    engine.ingestion_jobs.shutdown()
    engine.execution.shutdown()


def test_invalid_document_fails_once_with_useful_error(tmp_path):
    provider = FakeAIProvider()
    engine = PyDebuggerEngine(
        make_settings(tmp_path), provider, provider, DisabledExecutor()
    )
    job = engine.start_document_ingestion(
        "lesson.pdf", b"not a pdf", "application/pdf"
    )
    finished = wait_for(engine.get_ingestion, job.id)

    assert finished.state is JobState.FAILED
    assert finished.error_code == "invalid_document"
    assert "valid PDF" in finished.error
    assert finished.attempt == 1
    engine.ingestion_jobs.shutdown()
    engine.execution.shutdown()


def test_engine_background_youtube_ingestion_uses_same_job_contract(tmp_path):
    provider = FakeAIProvider()
    engine = PyDebuggerEngine(
        make_settings(tmp_path),
        provider,
        provider,
        DisabledExecutor(),
        FakeTranscriptProvider(),
    )
    job = engine.start_youtube_ingestion(
        "https://youtu.be/dQw4w9WgXcQ", ("en", "ta")
    )
    finished = wait_for(engine.get_ingestion, job.id)

    assert finished.state is JobState.COMPLETED
    assert finished.result.source_type == "youtube"
    assert finished.result.origin_uri.endswith("watch?v=dQw4w9WgXcQ")
    engine.ingestion_jobs.shutdown()
    engine.execution.shutdown()


def test_ingestion_retries_transient_failure_only(tmp_path):
    expected = source(tmp_path)
    attempts = 0

    def handler(filename, data, media_type, report, deadline, cancel_event):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise OSError("temporary storage failure")
        return expected

    service = IngestionJobService(
        handler, workers=1, queue_size=0, max_attempts=3
    )
    job = service.submit("lesson.txt", b"lesson")
    finished = wait_for(service.get, job.id)
    service.shutdown()

    assert finished.state is JobState.COMPLETED
    assert finished.attempt == 3
    assert attempts == 3


def test_ingestion_timeout_has_stable_error_code():
    def handler(filename, data, media_type, report, deadline, cancel_event):
        raise JobTimeoutError("Document ingestion exceeded its configured deadline")

    service = IngestionJobService(handler, workers=1, queue_size=0)
    job = service.submit("lesson.txt", b"lesson")
    finished = wait_for(service.get, job.id)
    service.shutdown()

    assert finished.state is JobState.TIMED_OUT
    assert finished.error_code == "ingestion_timeout"
    assert "deadline" in finished.error


def test_ingestion_cancellation_and_bounded_queue():
    started = Event()

    def handler(filename, data, media_type, report, deadline, cancel_event):
        started.set()
        cancel_event.wait(timeout=2)
        raise JobCancelledError("cancelled")

    service = IngestionJobService(handler, workers=1, queue_size=0)
    first = service.submit("one.txt", b"one")
    assert started.wait(timeout=1)
    with pytest.raises(JobQueueFullError, match="queue is full"):
        service.submit("two.txt", b"two")
    assert service.cancel(first.id)
    finished = wait_for(service.get, first.id)
    service.shutdown()

    assert finished.state is JobState.CANCELLED
    assert finished.error_code == "cancelled"


class FlakyExecutor:
    def __init__(self, result=None):
        self.calls = 0
        self.result = result

    def execute(self, source, execution_id):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("container runtime temporarily unavailable")
        return self.result or ExecutionResult(execution_id=execution_id, exit_code=0)

    def cancel(self, execution_id):
        return False


def test_execution_retries_infrastructure_error_and_reports_attempts():
    executor = FlakyExecutor()
    service = ExecutionService(executor, workers=1, queue_size=0, max_attempts=2)
    job = service.submit("print('ok')")
    finished = wait_for(service.get, job.id)
    service.shutdown()

    assert finished.state is JobState.COMPLETED
    assert finished.attempt == 2
    assert finished.progress == 1.0


def test_execution_timeout_is_a_distinct_terminal_state():
    result = ExecutionResult(
        execution_id="ignored", timed_out=True, stderr="Execution exceeded 1 second."
    )
    executor = FlakyExecutor(result)
    executor.calls = 1
    service = ExecutionService(executor, workers=1, queue_size=0)
    job = service.submit("while True: pass")
    finished = wait_for(service.get, job.id)
    service.shutdown()

    assert finished.state is JobState.TIMED_OUT
    assert finished.error_code == "execution_timeout"
    assert "exceeded" in finished.error
