from __future__ import annotations

from dataclasses import replace

from pydebugger.engine import PyDebuggerEngine
from pydebugger.execution import DisabledExecutor
from pydebugger.providers import ChatCompletion
from pydebugger.quality import EngineEvaluator, EvaluationCase

from test_core_engine import FakeAIProvider, make_settings


class QualityProvider(FakeAIProvider):
    @staticmethod
    def _vector(text):
        lowered = text.lower()
        return [
            float("loop" in lowered or "break" in lowered),
            float("valueerror" in lowered or "except" in lowered),
            float("database" in lowered or "sharding" in lowered),
        ]

    def complete_with_usage(self, system_prompt, user_prompt):
        lowered = user_prompt.lower()
        if "valueerror" in lowered:
            answer = "Use an except ValueError clause [1]."
        else:
            answer = "Use break to stop the loop early [1]."
        return ChatCompletion(answer, 40, 10, "quality-test-chat")


def test_real_engine_quality_baseline(tmp_path):
    provider = QualityProvider()
    settings = replace(make_settings(tmp_path), retrieval_min_score=0.95)
    engine = PyDebuggerEngine(settings, provider, provider, DisabledExecutor())
    engine.add_document(
        "loops.txt", b"A Python loop repeats work. Break exits a loop early."
    )
    engine.add_document(
        "exceptions.txt", b"An except ValueError clause handles invalid values."
    )
    cases = [
        EvaluationCase("loop", "How do I stop a loop early?", "loops.txt", True, ("break",)),
        EvaluationCase(
            "error", "How do I handle ValueError?", "exceptions.txt", True, ("except",)
        ),
        EvaluationCase(
            "unsupported",
            "Explain database sharding",
            None,
            False,
            ("supporting evidence",),
        ),
    ]

    report = EngineEvaluator(engine).run(cases)

    assert report.total_cases == 3
    assert report.pass_rate == 1.0
    assert report.grounding_accuracy == 1.0
    assert report.source_hit_rate == 1.0
    assert report.citation_traceability_rate == 1.0
    assert report.average_latency_ms > 0
    assert all(item.failure is None for item in report.cases)
    engine.ingestion_jobs.shutdown()
    engine.execution.shutdown()
