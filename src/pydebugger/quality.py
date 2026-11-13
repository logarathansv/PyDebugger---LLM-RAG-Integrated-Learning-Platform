"""Measured end-to-end quality evaluation for the supported engine."""

from __future__ import annotations

from dataclasses import dataclass
import time

from .engine import PyDebuggerEngine
from .models import AssistantMode


@dataclass(frozen=True)
class EvaluationCase:
    id: str
    question: str
    expected_source: str | None
    expected_grounded: bool
    required_answer_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class CaseResult:
    id: str
    passed: bool
    grounded_correct: bool
    source_hit: bool
    citations_traceable: bool
    answer_terms_found: bool
    latency_ms: float
    failure: str | None = None


@dataclass(frozen=True)
class EvaluationReport:
    total_cases: int
    passed_cases: int
    pass_rate: float
    grounding_accuracy: float
    source_hit_rate: float
    citation_traceability_rate: float
    answer_term_rate: float
    average_latency_ms: float
    p95_latency_ms: float
    cases: tuple[CaseResult, ...]


class EngineEvaluator:
    """Exercise the same engine entry point used by the application."""

    def __init__(self, engine: PyDebuggerEngine) -> None:
        self.engine = engine

    def run(
        self,
        cases: list[EvaluationCase],
        mode: AssistantMode = AssistantMode.PROGRAMMING_TUTOR,
    ) -> EvaluationReport:
        if not cases:
            raise ValueError("At least one evaluation case is required")
        documents = self.engine.list_documents()
        selected_ids = {document.id for document in documents}
        if not selected_ids:
            raise ValueError("Evaluation requires at least one indexed document")
        results: list[CaseResult] = []
        for case in cases:
            started = time.perf_counter()
            try:
                response = self.engine.answer(case.question, mode, selected_ids)
                latency_ms = (time.perf_counter() - started) * 1000
                grounded_correct = response.grounded is case.expected_grounded
                source_hit = (
                    True
                    if case.expected_source is None
                    else any(
                        citation.source_name == case.expected_source
                        for citation in response.citations
                    )
                )
                citations_traceable = all(
                    citation.document_id
                    and citation.chunk_id
                    and citation.score is not None
                    and citation.location
                    for citation in response.citations
                ) and (bool(response.citations) if response.grounded else True)
                lowered_answer = response.answer.lower()
                answer_terms_found = all(
                    term.lower() in lowered_answer for term in case.required_answer_terms
                )
                passed = all(
                    (grounded_correct, source_hit, citations_traceable, answer_terms_found)
                )
                results.append(
                    CaseResult(
                        case.id,
                        passed,
                        grounded_correct,
                        source_hit,
                        citations_traceable,
                        answer_terms_found,
                        latency_ms,
                    )
                )
            except Exception as error:
                results.append(
                    CaseResult(
                        case.id,
                        False,
                        False,
                        False,
                        False,
                        False,
                        (time.perf_counter() - started) * 1000,
                        str(error).strip() or type(error).__name__,
                    )
                )
        return self._report(results)

    @staticmethod
    def _report(results: list[CaseResult]) -> EvaluationReport:
        count = len(results)
        latencies = sorted(result.latency_ms for result in results)
        p95_index = max(0, min(count - 1, int(0.95 * count + 0.999) - 1))
        rate = lambda attribute: sum(bool(getattr(item, attribute)) for item in results) / count
        passed = sum(result.passed for result in results)
        return EvaluationReport(
            total_cases=count,
            passed_cases=passed,
            pass_rate=passed / count,
            grounding_accuracy=rate("grounded_correct"),
            source_hit_rate=rate("source_hit"),
            citation_traceability_rate=rate("citations_traceable"),
            answer_term_rate=rate("answer_terms_found"),
            average_latency_ms=sum(latencies) / count,
            p95_latency_ms=latencies[p95_index],
            cases=tuple(results),
        )
