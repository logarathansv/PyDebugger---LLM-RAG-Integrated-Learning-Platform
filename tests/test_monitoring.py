from __future__ import annotations

from dataclasses import replace

import pytest

from pydebugger import AssistantMode
from pydebugger.engine import PyDebuggerEngine
from pydebugger.execution import DisabledExecutor
from pydebugger.monitoring import MonitoringStore, OperationMetric
from pydebugger.monitoring import MonitoredChatProvider
from pydebugger.providers import ChatCompletion

from test_core_engine import FakeAIProvider, make_settings


class UsageProvider(FakeAIProvider):
    def complete_with_usage(self, system_prompt, user_prompt):
        self.last_system = system_prompt
        self.last_user = user_prompt
        return ChatCompletion(
            "A loop repeats work [1].",
            input_tokens=100,
            output_tokens=20,
            model="classroom-chat-v1",
        )


class FailingChat:
    def complete(self, system_prompt, user_prompt):
        raise TimeoutError("model deadline")


def test_monitoring_store_persists_aggregates_and_cost(tmp_path):
    path = tmp_path / "metrics.sqlite3"
    store = MonitoringStore(path, chat_input_cost_per_million=2.0)
    store.record(
        OperationMetric(
            "model.chat", "success", 10, input_tokens=100, cost_usd=0.0002,
            cost_configured=True,
        )
    )
    store.record(OperationMetric("model.chat", "failure", 30, error_code="Timeout"))

    summary = MonitoringStore(path).summaries(since_seconds=None)[0]

    assert summary.calls == 2
    assert summary.failures == 1
    assert summary.average_latency_ms == 20
    assert summary.p95_latency_ms == 30
    assert summary.input_tokens == 100
    assert summary.cost_usd == pytest.approx(0.0002)


def test_model_failure_is_recorded_without_prompt_content(tmp_path):
    store = MonitoringStore(tmp_path / "metrics.sqlite3")
    provider = MonitoredChatProvider(FailingChat(), store)

    with pytest.raises(TimeoutError):
        provider.complete("SECRET-SYSTEM", "SECRET-STUDENT-CONTENT")

    summary = store.summaries(since_seconds=None)[0]
    assert summary.operation == "model.chat"
    assert summary.failures == 1
    assert "SECRET" not in (tmp_path / "metrics.sqlite3").read_bytes().decode(
        "utf-8", errors="ignore"
    )


def test_monitoring_write_failure_does_not_break_model_response(tmp_path):
    store = MonitoringStore(tmp_path / "metrics.sqlite3")
    store.path = tmp_path  # Opening a directory as SQLite deterministically fails.
    provider = MonitoredChatProvider(UsageProvider(), store)

    answer = provider.complete("system", "question")

    assert "loop" in answer
    assert store.last_error and "Monitoring write failed" in store.last_error


def test_engine_records_real_operations_usage_failures_and_cost(tmp_path):
    provider = UsageProvider()
    settings = replace(
        make_settings(tmp_path),
        retrieval_min_score=0.2,
        chat_input_cost_per_million=2.0,
        chat_output_cost_per_million=4.0,
    )
    engine = PyDebuggerEngine(settings, provider, provider, DisabledExecutor())
    source = engine.add_document("loops.txt", b"A loop repeats work.")
    engine.answer("Explain the loop", AssistantMode.PROGRAMMING_TUTOR, {source.id})
    with pytest.raises(ValueError):
        engine.answer("No selection", AssistantMode.PROGRAMMING_TUTOR, set())

    summaries = {item.operation: item for item in engine.monitoring_summary(since_seconds=None)}

    assert summaries["ingestion.document"].calls == 1
    assert summaries["model.embedding.documents"].embedding_tokens > 0
    assert summaries["model.embedding.query"].calls == 1
    assert summaries["model.chat"].input_tokens == 100
    assert summaries["model.chat"].output_tokens == 20
    assert summaries["model.chat"].cost_usd == pytest.approx(0.00028)
    assert summaries["tutor.answer"].calls == 2
    assert summaries["tutor.answer"].failures == 1
    engine.ingestion_jobs.shutdown()
    engine.execution.shutdown()
