# Project-CIP

A compact README for the Project-CIP repository. This document describes the project's objective, workflow, technology stack, deployment, repository layout, how to run the code locally, and helpful notes.

## Objective

Project-CIP implements an information retrieval and RAG (Retrieval-Augmented Generation) toolkit and experiments built around LangChain, Chroma vector stores, and custom scripts to extract, index and query documents (PDFs and other sources). The project appears to support:

-   Document ingestion and preprocessing
-   Building a Chroma vector database from documents
-   Running RAG-style question-answering and classification experiments
-   Small utilities for evaluation and YouTube / web scraping related workflows

The repository is primarily used for research, prototyping, and demonstration of retrieval + LLM workflows.

## Deployed demo

A demo is deployed on Posit Cloud at:

https://logarathan-pydebugger.share.connect.posit.cloud/

Visit the URL to see interactive notebooks or web interfaces (hosted by Posit) that are part of this project.

## High-level workflow

1. Data collection & preprocessing
    - Extract text from PDFs or other source files (scripts under `webscrapping/`, `preprocessing/`, and `deep-api/`).
2. Embedding & vector store creation
    - Convert cleaned text into embeddings and store them in a Chroma vector DB (`langchain-openai/chroma_db/` and `chroma_db/`).
3. Retrieval & RAG pipeline
    - Use LangChain-based pipelines and custom RAG scripts (`langchain-openai/rag_*`, `deep-api/`) to retrieve context and generate answers with an LLM.
4. Evaluation & metrics
    - Run evaluation scripts (`langchain-openai/evaluation.py`) and visualize confusion matrices and classification metrics.
5. Deployment
    - Host notebooks or small web UIs on Posit Cloud for interactive demos.

## Tech stack

-   Python 3.x
-   LangChain (for orchestration of embedding, retrieval, and generation)
-   OpenAI (or other LLM provider) for text generation via LangChain connectors
-   Chroma DB for vector storage and similarity search
-   Common Python libraries: numpy, pandas, pdf parsing libraries (PyPDF2, pdfminer.six or similar), scikit-learn (for metrics), matplotlib/ seaborn (for plots)
-   Posit Cloud (deployment platform for notebooks / Shiny / Plumber apps)

See `requirements.txt` for the dependencies present in this repository.

## Repository structure (selected files & folders)

-   `deep-api/` — scripts for deeper RAG experiments and APIs (`deepseek.py`, `rag_deep.py`).
-   `langchain-openai/` — primary LangChain integration, RAG scripts, evaluation tools, and the Chroma DB folder.
    -   `chroma_db/` — Chroma database files and stored vectors (binary files and sqlite DBs).
    -   `evaluation.py` — evaluation and metrics code.
    -   `rag_chatbot_chroma.py`, `rag_with_chatbot.py` — RAG chatbot examples.
-   `webscrapping/` — scraping and preprocessing helper scripts and notes.
-   `preprocessing/` — text processing and cleaning scripts.
-   `PyDebug RAG/` — a separate folder containing vector DBs and example PDFs.
-   `requirements.txt` — Python dependency list.

Note: The repo contains several large binary files (Chroma DB bins) under `langchain-openai/chroma_db/` and `chroma_db/`. These are the persisted vector store files used by local experiments.

## How to run locally (quick start)

1. Create a Python virtual environment and activate it (Windows cmd example):

```cmd
python -m venv .venv
.venv\Scripts\activate
```

2. Install dependencies:

```cmd
pip install -r requirements.txt
```

3. Open a script or notebook to run. Example: run the main LangChain demo from `langchain-openai/main.py` or experiment with `langchain-openai/rag_with_chatbot.py`.

4. If you need to rebuild the Chroma DB, run the ingestion/preprocessing scripts (look for PDF processing and vectorization code in `preprocessing/` and `langchain-openai/`).

## Notes & best practices

-   Sensitive keys: do not hard-code API keys in repository files. Use environment variables or a secrets manager.
-   Chroma vector DB files can be large. Back them up separately and avoid committing large binary blobs to git unless necessary.
-   If you plan to re-deploy on Posit or another host, pin dependency versions in `requirements.txt` to improve reproducibility.

## Troubleshooting

-   Missing packages or import errors: re-check `requirements.txt` and install missing packages into the virtual environment.
-   Chroma DB access errors: ensure file permissions and correct working directory when loading `chroma_db`.

## Credits

Author/maintainer: logarathansv

If you want this README adjusted (add examples, screenshots, or detailed run commands for specific scripts), tell me which script or notebook you want documented and I'll add it.
