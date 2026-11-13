"""Durable, low-cardinality operational metrics for the supported engine."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from threading import Lock
import time
from typing import Any, Callable, Sequence

from .providers import ChatCompletion, ChatProvider, EmbeddingProvider


@dataclass(frozen=True)
class OperationMetric:
    operation: str
    status: str
    latency_ms: float
    error_code: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    embedding_tokens: int = 0
    cost_usd: float = 0.0
    cost_configured: bool = False
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class MetricSummary:
    operation: str
    calls: int
    failures: int
    average_latency_ms: float
    p95_latency_ms: float
    input_tokens: int
    output_tokens: int
    embedding_tokens: int
    cost_usd: float
    cost_configured: bool


class MonitoringStore:
    def __init__(
        self,
        path: Path,
        *,
        chat_input_cost_per_million: float = 0.0,
        chat_output_cost_per_million: float = 0.0,
        embedding_cost_per_million: float = 0.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.chat_input_rate = chat_input_cost_per_million
        self.chat_output_rate = chat_output_cost_per_million
        self.embedding_rate = embedding_cost_per_million
        self.chat_cost_configured = (
            self.chat_input_rate > 0 or self.chat_output_rate > 0
        )
        self.embedding_cost_configured = self.embedding_rate > 0
        self.cost_configured = any(
            rate > 0
            for rate in (self.chat_input_rate, self.chat_output_rate, self.embedding_rate)
        )
        self._clock = clock
        self._write_lock = Lock()
        self.last_error: str | None = None
        self._initialize()

    def record(self, metric: OperationMetric) -> bool:
        metadata = json.dumps(metric.metadata or {}, sort_keys=True, ensure_ascii=False)
        try:
            with self._write_lock, self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO operation_metrics (
                        recorded_at, operation, status, latency_ms, error_code,
                        input_tokens, output_tokens, embedding_tokens, cost_usd,
                        cost_configured, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self._clock(),
                        metric.operation,
                        metric.status,
                        max(0.0, metric.latency_ms),
                        metric.error_code,
                        max(0, metric.input_tokens),
                        max(0, metric.output_tokens),
                        max(0, metric.embedding_tokens),
                        max(0.0, metric.cost_usd),
                        int(metric.cost_configured),
                        metadata,
                    ),
                )
            self.last_error = None
            return True
        except sqlite3.Error as error:
            self.last_error = f"Monitoring write failed: {error}"
            return False

    def summaries(self, *, since_seconds: float | None = 86_400) -> tuple[MetricSummary, ...]:
        query = (
            "SELECT operation, status, latency_ms, input_tokens, output_tokens, "
            "embedding_tokens, cost_usd, cost_configured FROM operation_metrics"
        )
        parameters: tuple[float, ...] = ()
        if since_seconds is not None:
            query += " WHERE recorded_at >= ?"
            parameters = (self._clock() - since_seconds,)
        try:
            with self._connect() as connection:
                rows = connection.execute(query, parameters).fetchall()
            self.last_error = None
        except sqlite3.Error as error:
            self.last_error = f"Monitoring read failed: {error}"
            return ()
        grouped: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            grouped.setdefault(str(row["operation"]), []).append(row)
        summaries: list[MetricSummary] = []
        for operation, items in sorted(grouped.items()):
            latencies = sorted(float(item["latency_ms"]) for item in items)
            p95_index = max(0, min(len(latencies) - 1, int(0.95 * len(latencies) + 0.999) - 1))
            summaries.append(
                MetricSummary(
                    operation=operation,
                    calls=len(items),
                    failures=sum(item["status"] != "success" for item in items),
                    average_latency_ms=sum(latencies) / len(latencies),
                    p95_latency_ms=latencies[p95_index],
                    input_tokens=sum(int(item["input_tokens"]) for item in items),
                    output_tokens=sum(int(item["output_tokens"]) for item in items),
                    embedding_tokens=sum(int(item["embedding_tokens"]) for item in items),
                    cost_usd=sum(float(item["cost_usd"]) for item in items),
                    cost_configured=any(bool(item["cost_configured"]) for item in items),
                )
            )
        return tuple(summaries)

    def chat_cost(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens * self.chat_input_rate
            + output_tokens * self.chat_output_rate
        ) / 1_000_000

    def embedding_cost(self, tokens: int) -> float:
        return tokens * self.embedding_rate / 1_000_000

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS operation_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recorded_at REAL NOT NULL,
                    operation TEXT NOT NULL,
                    status TEXT NOT NULL,
                    latency_ms REAL NOT NULL,
                    error_code TEXT,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    embedding_tokens INTEGER NOT NULL DEFAULT 0,
                    cost_usd REAL NOT NULL DEFAULT 0,
                    cost_configured INTEGER NOT NULL DEFAULT 0,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_metrics_time_operation "
                "ON operation_metrics(recorded_at, operation)"
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection


class MonitoredChatProvider:
    def __init__(self, provider: ChatProvider, store: MonitoringStore) -> None:
        self._provider = provider
        self._store = store

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        started = time.perf_counter()
        try:
            detailed = getattr(self._provider, "complete_with_usage", None)
            if callable(detailed):
                result: ChatCompletion = detailed(system_prompt, user_prompt)
            else:
                result = ChatCompletion(
                    text=self._provider.complete(system_prompt, user_prompt),
                    model=None,
                )
            latency = (time.perf_counter() - started) * 1000
            cost = self._store.chat_cost(result.input_tokens, result.output_tokens)
            self._store.record(
                OperationMetric(
                    "model.chat",
                    "success",
                    latency,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    cost_usd=cost,
                    cost_configured=self._store.chat_cost_configured,
                    metadata={"model": result.model or "unknown"},
                )
            )
            return result.text
        except Exception as error:
            self._store.record(
                OperationMetric(
                    "model.chat",
                    "failure",
                    (time.perf_counter() - started) * 1000,
                    error_code=type(error).__name__,
                )
            )
            raise


class MonitoredEmbeddingProvider:
    def __init__(self, provider: EmbeddingProvider, store: MonitoringStore) -> None:
        self._provider = provider
        self._store = store

    @property
    def embedding_identity(self) -> str:
        return self._provider.embedding_identity

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._call("model.embedding.documents", texts, self._provider.embed_documents)

    def embed_query(self, text: str) -> list[float]:
        return self._call("model.embedding.query", [text], lambda values: self._provider.embed_query(values[0]))

    def _call(self, operation: str, texts: Sequence[str], function):
        started = time.perf_counter()
        # Embedding APIs do not expose usage through LangChain; label this estimate.
        estimated_tokens = sum(max(1, (len(text) + 3) // 4) for text in texts)
        try:
            result = function(texts)
            self._store.record(
                OperationMetric(
                    operation,
                    "success",
                    (time.perf_counter() - started) * 1000,
                    embedding_tokens=estimated_tokens,
                    cost_usd=self._store.embedding_cost(estimated_tokens),
                    cost_configured=self._store.embedding_cost_configured,
                    metadata={
                        "token_count": "estimated",
                        "items": len(texts),
                        "model": self.embedding_identity,
                    },
                )
            )
            return result
        except Exception as error:
            self._store.record(
                OperationMetric(
                    operation,
                    "failure",
                    (time.perf_counter() - started) * 1000,
                    error_code=type(error).__name__,
                    embedding_tokens=estimated_tokens,
                    metadata={
                        "token_count": "estimated",
                        "items": len(texts),
                        "model": self.embedding_identity,
                    },
                )
            )
            raise
