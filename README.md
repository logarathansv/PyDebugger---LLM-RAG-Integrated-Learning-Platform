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
- `tutoring.py` — tutor prompts, citations, history, and progressive hints
- `execution.py` — asynchronous, cancellable Docker execution jobs
- `engine.py` — the single composition root used by the application

The current vector index is intentionally in memory for the first consolidation
milestone. Durable storage, authentication, course ownership, and permissions
can be added later behind these service boundaries.

Uploads support validated PDF, UTF-8 text, CSV, PPTX, Markdown, and restricted
reStructuredText. Chunks retain file hashes, media types, page/slide/row or
heading locations, line and character ranges, and source origin. YouTube URLs or
video IDs can be indexed from available transcripts with timestamp and language
metadata. Transcript availability depends on YouTube and may be blocked from
some cloud-hosting IP ranges.

The validation rules, parser limits, metadata contract, and transcript behavior
are documented in `docs/INGESTION.md`.

## Run the supported application

Python 3.13 is the supported runtime. `uv.lock` pins the complete dependency
graph, and `.python-version` pins the Python patch release.

```powershell
uv sync --locked --extra dev
Copy-Item .env.example .env
uv run --locked streamlit run app.py
```

Populate `.env` with an Azure OpenAI base endpoint, API key, chat deployment,
and embedding deployment. The endpoint should be the resource base URL, not a
deployment-specific request URL.

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

## Supported document flow

1. `PyDebuggerEngine.add_document()` validates and stores the upload.
2. `DocumentIngestor` extracts text and preserves source locations.
3. `InMemoryVectorIndex` embeds chunks and ranks matches across all selected files.
4. `TutorService` builds a bounded prompt with conversation history and citations.
5. The configured `ChatProvider` generates the response.

Supported uploads are PDF, TXT, Markdown, reStructuredText, CSV, and PPTX.
reStructuredText raw directives and external file insertion are disabled.

## Current milestone boundaries

- The document catalog and vector index live for the current app session only.
- Azure OpenAI is the supported provider adapter.
- Code execution is off unless the Docker backend and pinned image pass startup checks.
- Authentication, courses, enrollment, durable student history, assignments,
  and instructor dashboards are planned after the core engine is strengthened.

## Legacy experiments

Install `requirements-legacy.txt` only when reproducing the older experimental
scripts. They use separate app flows and are not the supported production path.
Some legacy documentation in `USAGE.md` describes those earlier scripts.
