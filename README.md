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
- `retrieval.py` — vector indexing and corpus-wide ranking
- `tutoring.py` — tutor prompts, citations, history, and progressive hints
- `execution.py` — code-execution interface and disabled-by-default local runner
- `engine.py` — the single composition root used by the application

The current vector index is intentionally in memory for the first consolidation
milestone. Durable storage, authentication, course ownership, and permissions
can be added later behind these service boundaries.

## Run the supported application

Python 3.10 or newer is required.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
Copy-Item .env.example .env
streamlit run app.py
```

Populate `.env` with an Azure OpenAI base endpoint, API key, chat deployment,
and embedding deployment. The endpoint should be the resource base URL, not a
deployment-specific request URL.

Code execution is disabled by default. Setting
`PYDEBUGGER_ALLOW_LOCAL_EXECUTION=true` enables a subprocess runner with an
execution deadline and response truncation for trusted local development. It is
not a security sandbox and must not be exposed to untrusted classroom users.

## Test the backend

```powershell
python -m pip install -e ".[dev]"
python -m pytest
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
- Code execution is off unless explicitly enabled for trusted development.
- Authentication, courses, enrollment, durable student history, assignments,
  and instructor dashboards are planned after the core engine is strengthened.

## Legacy experiments

Install `requirements-legacy.txt` only when reproducing the older experimental
scripts. They use separate app flows and are not the supported production path.
Some legacy documentation in `USAGE.md` describes those earlier scripts.
