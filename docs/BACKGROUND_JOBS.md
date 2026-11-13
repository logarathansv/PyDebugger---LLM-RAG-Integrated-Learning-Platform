# Background jobs and failures

PyDebugger uses bounded in-process worker pools for document ingestion and
isolated code execution. Worker and queue capacity are controlled by
`PYDEBUGGER_BACKGROUND_WORKERS` and `PYDEBUGGER_BACKGROUND_QUEUE_SIZE`. A full
queue rejects new work with an actionable error instead of creating unbounded
threads or retaining unlimited upload data in memory.

## Observable lifecycle

Both job types use these states:

- `queued` — accepted but not yet running
- `running` — work is active
- `retrying` — a transient infrastructure operation failed and another bounded
  attempt will begin
- `completed` — the result committed successfully
- `failed` — a non-retryable error or exhausted retry budget
- `cancelled` — cancellation succeeded before the commit point
- `timed_out` — the configured deadline was exceeded

Every snapshot includes a progress value from 0 to 1, a human-readable phase,
the current and maximum attempt counts, and a stable error code when applicable.

## Ingestion guarantees

Upload and transcript jobs use `PYDEBUGGER_INGESTION_TIMEOUT_SECONDS` and
`PYDEBUGGER_INGESTION_MAX_ATTEMPTS`. Validation errors are never retried.
Transient storage and vector-index failures are retried with bounded backoff.
Cancellation and deadline checks occur between validation, storage, extraction,
and indexing phases. External transcript and AI clients retain their own request
timeouts so a network call is also bounded.

Index replacement is the commit point. Before it, cancellation, timeout, or
failure cleans up the candidate file and preserves the active revision. Once a
replacement commits, the job finishes rather than reporting cancellation for a
revision that is already searchable. Operations for the same logical document
are serialized, preventing concurrent replacement and deletion races.

## Execution guarantees

The Docker executor enforces the execution deadline and resource limits. The
job service retries only raised infrastructure failures, up to
`PYDEBUGGER_EXECUTION_MAX_ATTEMPTS`; learner syntax errors, runtime errors,
timeouts, and non-zero exits are terminal and are not rerun. Execution remains
isolated, network-disabled, and disposable on every attempt.

These queues are process-local. A multi-instance deployment should replace this
service with a durable external queue while retaining the public job contracts.
