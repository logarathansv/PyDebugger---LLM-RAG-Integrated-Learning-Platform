"""Run a JSON evaluation set through the real supported PyDebugger engine."""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dotenv import load_dotenv

from pydebugger import Settings
from pydebugger.engine import PyDebuggerEngine
from pydebugger.quality import EngineEvaluator, EvaluationCase


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.dataset.read_text(encoding="utf-8"))
    load_dotenv(ROOT / ".env")
    base_settings = Settings.from_env(ROOT)
    with tempfile.TemporaryDirectory(prefix="pydebugger-evaluation-") as directory:
        evaluation_dir = Path(directory)
        settings = replace(
            base_settings,
            data_dir=evaluation_dir / "data",
            vector_dir=evaluation_dir / "vectors",
            monitoring_db_path=evaluation_dir / "monitoring.sqlite3",
        )
        engine = PyDebuggerEngine(settings)
        for document in payload["documents"]:
            engine.add_document(
                document["filename"],
                document["content"].encode("utf-8"),
                document.get("media_type", "text/plain"),
            )
        cases = [
            EvaluationCase(
                id=item["id"],
                question=item["question"],
                expected_source=item.get("expected_source"),
                expected_grounded=bool(item["expected_grounded"]),
                required_answer_terms=tuple(item.get("required_answer_terms", [])),
            )
            for item in payload["cases"]
        ]
        report = asdict(EngineEvaluator(engine).run(cases))
        engine.ingestion_jobs.shutdown()
        engine.execution.shutdown()
    rendered = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0 if report["pass_rate"] >= float(payload.get("minimum_pass_rate", 1.0)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
