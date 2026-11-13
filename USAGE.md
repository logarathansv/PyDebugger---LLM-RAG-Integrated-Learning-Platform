# Legacy Usage and Deployment Guide — Project-CIP

> This guide documents the experimental scripts under `langchain-openai/`.
> For the supported consolidated application, follow `README.md` and run
> `streamlit run app.py`.

This document expands the quick-start items in `README.md` with concrete steps for running the main RAG scripts, rebuilding the Chroma vector store, setting environment variables, and notes for deploying to Posit Cloud.

## Prerequisites

-   Python 3.8+ installed
-   Git (optional)
-   Access to an LLM provider (e.g., OpenAI) with an API key
-   `requirements.txt` should be present in the repository root

Create and activate a virtual environment (Windows / cmd):

```cmd
python -m venv .venv
.venv\Scripts\activate
```

Install dependencies:

```cmd
pip install -r requirements.txt
```

## Environment variables

Set your API keys and configuration in environment variables. Example variables used by common scripts:

-   `OPENAI_API_KEY` — OpenAI API key (if using OpenAI)
-   `CHROMA_DB_DIR` — Path to a Chroma DB directory (optional; some scripts assume default folders)

Windows cmd example:

```cmd
set OPENAI_API_KEY=sk-xxxxx
set CHROMA_DB_DIR=langchain-openai/chroma_db
```

Switch to PowerShell or a `.env` loader if you prefer persistent environment variable management.

## Running the main scripts

1. Run the main LangChain demo

Open and inspect `langchain-openai/main.py` to confirm the configuration and which functions it calls. Run:

```cmd
python langchain-openai\main.py
```

2. Run the chatbot/RAG demo

Example files:

-   `langchain-openai/rag_with_chatbot.py`
-   `langchain-openai/rag_chatbot_chroma.py`

Run the selected script (example):

```cmd
python langchain-openai\rag_with_chatbot.py
```

Note: If a script expects an interactive session (e.g., launching a web UI), open it in the appropriate environment (Jupyter / notebook server) or follow the script's console prompts.

## Rebuilding or creating the Chroma vector store

1. Preprocess source documents
    - Look in `preprocessing/` and `webscrapping/` for text extraction scripts and adapt them to point to your data folder.
2. Run the embedding/ingestion script
    - Search for ingestion or vectorization logic (commonly in `langchain-openai/` or `deep-api/`). An example ingestion command might be:

```cmd
python langchain-openai\ingest.py --source docs/ --chroma_dir langchain-openai/chroma_db
```

If there is no `ingest.py` present, open `langchain-openai/` files like `rag-deep.py`, `rag_deep.py`, `rag_with_chatbot.py` and locate the code that builds embeddings and calls `Chroma` or the embedding API. Adapt and run that block as a small script.

## Posit Cloud deployment notes

The project has an interactive demo deployed on Posit Cloud at:

https://logarathan-pydebugger.share.connect.posit.cloud/

If you want to deploy your own copy to Posit Cloud (notebook or web app):

1. Ensure your repository contains the notebook or app file you want to run (`.Rmd`, `.ipynb`, or Python web app script).
2. Push the code to a Git repo (GitHub or similar) or use Posit's 'Upload' options.
3. On Posit Cloud, create a new project and connect to the repository or upload files.
4. Configure environment variables through Posit (API keys must be set in the Posit project settings) — do NOT commit API keys.
5. Use a `requirements.txt` or `install.R` (for R content) at the repo root so Posit can install dependencies on the server.

Posit-specific tips

-   Keep long-running or memory-heavy tasks (large Chroma DB builds) offline or use a background worker; Posit sessions can time out.
-   Use smaller sample datasets for demos to keep startup times short.

## Troubleshooting

-   If `chroma` imports fail, ensure `chromadb` (or the specific Chroma package referenced in the code) is installed and compatible with your Python version.
-   If an OpenAI request fails, check your `OPENAI_API_KEY`, network, and rate limits.
-   If the Posit deployment can't find dependencies, place a `requirements.txt` at the project root with pinned versions.

## Want a step-by-step for a specific script?

If you want, tell me which script or notebook you want a step-by-step walkthrough for (for example `langchain-openai/rag_with_chatbot.py` or `deep-api/rag_deep.py`) and I will add command examples, required environment variables, and expected outputs into this document.
