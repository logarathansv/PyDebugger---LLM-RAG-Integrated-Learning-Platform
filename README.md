# PyDebugger Learning Platform

PyDebugger is a document-grounded Python tutor and debugging assistant. The
supported application has one backend composition root and one Streamlit entry
point. Earlier scripts remain in `langchain-openai/` and `webscrapping/` as
historical experiments; new development should use `src/pydebugger/` and
`app.py`.

## Supported architecture

The backend is split into modules with narrow responsibilities:

- `config.py` — validated environment configuration
- `providers.py` — AI chat and embedding provider adapters
- `ingestion.py` — safe document extraction and chunking
- `youtube.py` — validated video references and transcript-provider adapter
- `retrieval.py` — vector indexing and corpus-wide ranking
- `routing.py` — bounded LangGraph request routing with rule-based classification
- `hints.py` — LangGraph hint progression and duplicate prevention
- `tutoring.py` — tutor prompts, citations, history, and progressive hints
- `execution.py` — asynchronous, cancellable Docker execution jobs
- `jobs.py` — bounded background queues, retries, deadlines, and progress
- `monitoring.py` — durable latency, failure, usage, and cost metrics
- `quality.py` — measured end-to-end engine evaluation
- `engine.py` — the single composition root used by the application

The supported vector backend is a local persistent Chroma collection. It records
the embedding identity and index schema, ranks matches across all selected
documents in one query, and returns stable chunk IDs and relevance scores for
traceable citations. The in-memory index exists only as a test double.

Uploads support validated PDF, UTF-8 text, CSV, PPTX, Markdown, and restricted
reStructuredText. Chunks retain file hashes, media types, page/slide/row or
heading locations, line and character ranges, and source origin. YouTube URLs or
video IDs can be indexed from available transcripts with timestamp and language
metadata. Transcript availability depends on YouTube and may be blocked from
some cloud-hosting IP ranges.

The validation rules, parser limits, metadata contract, and transcript behavior
are documented in `docs/INGESTION.md`.

Document uploads and YouTube transcripts run as background jobs with bounded
worker and queue capacity. Jobs expose progress, current phase, attempt count,
and stable terminal states. Invalid input fails immediately; transient storage
or indexing errors retry up to the configured limit. See
`docs/BACKGROUND_JOBS.md` for lifecycle and timeout semantics.

The Operations tab reads durable local metrics from SQLite. Chat-token counts
come from the model response; embedding-token counts are explicitly labeled
estimates because the embedding integration does not return usage. Cost is only
calculated after the deployment's contracted per-million-token rates are set in
configuration. See `docs/QUALITY_MONITORING.md`.

## Run the supported application

Python 3.13 is the supported runtime. `uv.lock` pins the complete dependency
graph, and `.python-version` pins the Python patch release.

### Windows step-by-step guide

1. Install the required tools:

   - Python 3.13
   - [uv](https://docs.astral.sh/uv/getting-started/installation/)
   - Docker Desktop only if you want to execute student code

2. Open PowerShell and enter the repository directory:

   ```powershell
   Set-Location "N:\CODE\Codex\PyDebugger---LLM-RAG-Integrated-Learning-Platform"
   ```

3. Install the exact locked dependencies:

   ```powershell
   uv sync --locked --extra dev
   ```

4. Create the local environment file if it does not already exist:

   ```powershell
   Copy-Item .env.example .env
   ```

5. Open `.env` and configure Google Gemini:

   ```dotenv
   PYDEBUGGER_AI_PROVIDER="gemini"
   GOOGLE_API_KEY="your-google-ai-studio-key"
   GOOGLE_CHAT_MODEL="gemini-3.5-flash-lite"
   GOOGLE_EMBEDDING_MODEL="gemini-embedding-001"
   ```

   Create a key at [Google AI Studio](https://aistudio.google.com/apikey). Never
   commit the `.env` file or share its API key.

6. Optional: enable the isolated Python lab. Start Docker Desktop, wait for the
   Docker engine to become ready, and run:

   ```powershell
   docker pull python:3.13-slim-bookworm@sha256:a5a7017efc5969e00ae2b91c8eb0631049a569652aa4ffc4501769049ae45c72
   ```

   Then set this value in `.env`:

   ```dotenv
   PYDEBUGGER_EXECUTION_BACKEND="docker"
   ```

   Keep `PYDEBUGGER_EXECUTION_BACKEND="disabled"` when Docker is unavailable.
   Tutoring and document retrieval will still work, but code execution will be
   disabled.

7. Start the application:

   ```powershell
   uv run --locked streamlit run app.py
   ```

8. Open [http://localhost:8501](http://localhost:8501) in your browser. To use a
   different port:

   ```powershell
   uv run --locked streamlit run app.py --server.port 8502
   ```

9. Verify the main workflows:

   - Upload a PDF, TXT, Markdown, CSV, RST, or PPTX document.
   - Select the indexed document and ask a question in **Tutor**.
   - Open **Python lab**, edit `main.py`, add files or tests, and select
     **Run project**.
   - Open **System health** to review operations, failures, latency, and usage.

10. Run the automated tests:

    ```powershell
    uv run --locked pytest -q
    uv lock --check
    ```

    Docker integration tests are optional and require the Docker engine plus the
    pinned image:

    ```powershell
    $env:PYDEBUGGER_RUN_DOCKER_TESTS="1"
    uv run --locked pytest -q tests\test_execution_docker.py
    ```

11. Stop the application by returning to its PowerShell window and pressing
    `Ctrl+C`.

### Common startup problems

- **Configuration error:** confirm `.env` contains a valid Gemini or Azure key.
- **Port already in use:** start with `--server.port 8502` or another free port.
- **Docker backend is not ready:** start Docker Desktop and confirm `docker version`
  succeeds.
- **Pinned image is missing:** repeat the `docker pull` command from step 6.
- **Old embeddings fail after changing models:** use a new
  `PYDEBUGGER_VECTOR_COLLECTION` value in `.env`.

```powershell
uv sync --locked --extra dev
Copy-Item .env.example .env
uv run --locked streamlit run app.py
```

Gemini through Google AI Studio is the default low-cost provider. Populate
`.env` with `GOOGLE_API_KEY`, `GOOGLE_CHAT_MODEL`, and
`GOOGLE_EMBEDDING_MODEL`. To use Azure instead, set
`PYDEBUGGER_AI_PROVIDER="azure_openai"` and configure its endpoint, API key,
chat deployment, and embedding deployment.

The current Gemini defaults are `gemini-3.5-flash-lite` for tutoring and
`gemini-embedding-001` for retrieval. Changing the embedding model requires a
new `PYDEBUGGER_VECTOR_COLLECTION` name so incompatible vectors are never mixed.

Token controls default to three retrieved chunks, a 2,500-token retrieval
context budget, a 750-token conversation budget (at most six relevant messages),
and 600 output tokens. Override `PYDEBUGGER_RETRIEVAL_LIMIT`,
`PYDEBUGGER_RETRIEVAL_CONTEXT_TOKENS`,
`PYDEBUGGER_CONVERSATION_CONTEXT_TOKENS`, or
`PYDEBUGGER_LLM_MAX_OUTPUT_TOKENS` when a course needs different limits. Request
routing and hint-stage updates are deterministic; they do not consume an extra
model call.

Configuration is validated before providers or storage are initialized. Invalid
URLs, placeholders, numeric limits, mutable execution images, and unavailable
Docker runtimes produce a clear startup error without printing secrets.

Code execution is disabled by default. To enable the isolated runner, start the
Docker Linux engine and pull the exact pinned image from `.env.example`:

```powershell
docker pull python:3.13-slim-bookworm@sha256:a5a7017efc5969e00ae2b91c8eb0631049a569652aa4ffc4501769049ae45c72
# Set PYDEBUGGER_EXECUTION_BACKEND="docker" in .env
uv run --locked streamlit run app.py
```

Each submission runs outside the app server in a disposable container with no
network, a read-only root filesystem and source mount, an unprivileged user, no
Linux capabilities, no-new-privileges, and CPU, memory, process, temporary
storage, output, source-size, and time limits. Runs can be cancelled and are
forcibly cleaned up. See `docs/EXECUTION_SECURITY.md` for the threat model and
deployment requirements.

Syntax and runtime failures are returned as structured diagnostics with the
original submitted line and column numbers. Runtime traces contain only learner
frames from `submission.py`; sandbox bootstrap frames are hidden. The supported
interface intentionally provides **Run** and **Cancel** only. Experimental pause,
step, breakpoint, and watch controls are not presented until a real interactive
debugger protocol is implemented.

## Test the backend

```powershell
uv run --locked --extra dev pytest
uv lock --check
```

The tests use fake AI providers, so they do not consume API credits.

To run the versioned baseline against the configured AI provider and the
real supported engine (this consumes model quota):

```powershell
uv run --locked python scripts/evaluate_engine.py evaluation/classroom_baseline.json `
  --output evaluation-result.json
```

The evaluation uses an isolated temporary corpus and exits non-zero when its
minimum pass rate is missed. The old random/mock evaluator was removed.

## Supported document flow

1. `PyDebuggerEngine.add_document()` validates and stores the upload.
2. `DocumentIngestor` extracts text and preserves source locations.
3. `ChromaVectorIndex` persists chunks and ranks matches across all selected files.
4. `TutorService` selects relevant history, enforces an evidence threshold, and
   builds citations. Unsupported questions do not call the chat model.
5. The configured `ChatProvider` generates the response.

Uploading the same normalized filename replaces that logical document. Its ID
stays stable, while its SHA-256 content hash and chunk index version change.
New chunks are indexed before obsolete chunks and files are removed; failed
replacement leaves the previous revision available. Deletion clears both the
vector records and the document's bounded storage directory.

Supported uploads are PDF, TXT, Markdown, reStructuredText, CSV, and PPTX.
reStructuredText raw directives and external file insertion are disabled.

## Current milestone boundaries

- Document files, catalog metadata, and vectors persist locally across restarts.
- Local Chroma is intended for a single application instance; multi-node
  deployment will require a shared vector service behind the same interface.
- Gemini and Azure OpenAI are supported provider adapters; Gemini is the default
  when `GOOGLE_API_KEY` is configured.
- Code execution is off unless the Docker backend and pinned image pass startup checks.
- Authentication, courses, enrollment, durable student history, assignments,
  and instructor dashboards are planned after the core engine is strengthened.

## Legacy experiments

Install `requirements-legacy.txt` only when reproducing the older experimental
scripts. They use separate app flows and are not the supported production path.
Some legacy documentation in `USAGE.md` describes those earlier scripts.
