# Quality checks and monitoring

## Engine evaluation

`scripts/evaluate_engine.py` loads a versioned JSON case set, creates an isolated
temporary Chroma corpus, ingests the declared documents through
`PyDebuggerEngine`, and asks every question through `PyDebuggerEngine.answer`.
It does not generate random predictions or substitute a classification mock.

Each case measures:

- whether the grounded/no-evidence decision is correct;
- whether citations include the expected source;
- whether citations retain document ID, chunk ID, score, and source location;
- whether required answer terms are present;
- end-to-end latency and any raised failure.

The report includes pass rate, grounding accuracy, source hit rate, citation
traceability, answer-term rate, average latency, p95 latency, and per-case
failures. The process exits non-zero below the dataset's `minimum_pass_rate`, so
it can be used as a release gate. Live evaluation consumes Azure model quota.

CI tests run the same evaluator and real engine pipeline with deterministic
local provider doubles. This tests ingestion, embedding, retrieval, evidence
gating, tutoring, and citations without network cost or nondeterministic model
answers.

## Runtime monitoring

Metrics are stored in the SQLite file configured by
`PYDEBUGGER_MONITORING_DB`. The database uses WAL mode and a low-cardinality
schema. Prompts, answers, document text, student code, API keys, and filenames
are not recorded.

Recorded operation families include document/transcript ingestion, retrieval
embeddings, chat completion, tutoring answers and hints, and synchronous or
background execution. Each event contains status, latency, stable error code,
token usage where available, calculated cost, and small operational metadata.
The Streamlit Operations tab aggregates calls, failures, average latency, p95
latency, usage, and cost for the last 24 hours.

LangChain chat responses expose standardized actual input and output token
counts. The embedding integration returns vectors without usage metadata, so
embedding tokens use a documented character-based estimate and are displayed
as estimated. They must not be interpreted as invoice-exact.

## Cost configuration

Pricing is deliberately not hard-coded. Set these values to the Azure rates
contracted for the exact configured deployments:

- `PYDEBUGGER_CHAT_INPUT_COST_PER_MILLION`
- `PYDEBUGGER_CHAT_OUTPUT_COST_PER_MILLION`
- `PYDEBUGGER_EMBEDDING_COST_PER_MILLION`

When every value is zero, token usage is still tracked but the UI reports cost
as not configured. Updating rates affects new events; historical event costs are
preserved as originally calculated.

For a multi-instance deployment, export the same metric contract to a shared
telemetry backend. The local SQLite implementation is intended for the current
single-instance classroom deployment.
