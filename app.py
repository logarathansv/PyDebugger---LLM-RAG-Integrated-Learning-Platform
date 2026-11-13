"""Supported Streamlit entry point for the consolidated backend."""

from __future__ import annotations

from pathlib import Path
import sys

import streamlit as st
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from pydebugger import (
    AssistantMode,
    ChatMessage,
    ExecutionState,
    PyDebuggerEngine,
    Settings,
)
from pydebugger.config import ConfigurationError
from pydebugger.ingestion import IngestionError


def get_engine() -> PyDebuggerEngine:
    if "engine" not in st.session_state:
        load_dotenv(PROJECT_ROOT / ".env")
        st.session_state.engine = PyDebuggerEngine(Settings.from_env(PROJECT_ROOT))
    return st.session_state.engine


st.set_page_config(page_title="PyDebugger", page_icon="🔧", layout="wide")
st.title("PyDebugger Learning Platform")

try:
    engine = get_engine()
except ConfigurationError as error:
    st.error(f"Configuration error: {error}")
    st.stop()
except Exception as error:
    st.error(f"Backend startup failed: {error}")
    st.stop()

if "messages" not in st.session_state:
    st.session_state.messages = []

with st.sidebar:
    st.header("Learning resources")
    uploads = st.file_uploader(
        "Upload course documents",
        type=["pdf", "txt", "md", "rst", "csv", "pptx"],
        accept_multiple_files=True,
    )
    if uploads and st.button("Index uploaded files"):
        for upload in uploads:
            try:
                engine.add_document(upload.name, upload.getvalue(), upload.type)
                st.success(f"Indexed {upload.name}")
            except IngestionError as error:
                st.error(f"{upload.name}: {error}")
            except Exception as error:
                st.error(f"Could not index {upload.name}: {error}")

    st.subheader("YouTube transcript")
    youtube_reference = st.text_input(
        "YouTube URL or video ID",
        placeholder="https://www.youtube.com/watch?v=...",
    )
    transcript_languages = st.text_input(
        "Preferred transcript languages",
        value="en",
        help="Comma-separated language codes in priority order, for example en,ta,hi.",
    )
    if st.button("Index YouTube transcript", disabled=not youtube_reference.strip()):
        try:
            languages = tuple(
                item.strip() for item in transcript_languages.split(",") if item.strip()
            )
            source = engine.add_youtube_transcript(youtube_reference, languages)
            st.success(f"Indexed {source.name}")
        except IngestionError as error:
            st.error(str(error))
        except Exception as error:
            st.error(f"Could not index the YouTube transcript: {error}")

    documents = engine.list_documents()
    selected_ids = {
        document.id
        for document in documents
        if st.checkbox(document.name, value=True, key=f"document-{document.id}")
    }
    if documents:
        delete_id = st.selectbox(
            "Remove a document",
            options=[""] + [document.id for document in documents],
            format_func=lambda value: "Select…"
            if not value
            else next(item.name for item in documents if item.id == value),
        )
        if delete_id and st.button("Remove selected document"):
            engine.remove_document(delete_id)
            st.rerun()

mode = AssistantMode(
    st.radio("Assistant mode", [item.value for item in AssistantMode], horizontal=True)
)
chat_tab, runner_tab = st.tabs(["Tutor", "Python runner"])

with chat_tab:
    for message in st.session_state.messages:
        with st.chat_message(message.role):
            st.markdown(message.content)
    query = st.chat_input("Ask a question about the selected material")
    if query:
        history = list(st.session_state.messages)
        st.session_state.messages.append(ChatMessage("user", query))
        with st.chat_message("user"):
            st.markdown(query)
        with st.chat_message("assistant"):
            try:
                with st.spinner("Finding relevant material…"):
                    response = engine.answer(query, mode, selected_ids, history)
                st.markdown(response.answer)
                if response.citations:
                    with st.expander("Sources used"):
                        for citation in response.citations:
                            location = f" — {citation.location}" if citation.location else ""
                            source_label = citation.source_name
                            if citation.source_uri:
                                source_label = f"[{source_label}]({citation.source_uri})"
                            st.markdown(f"**{source_label}{location}**")
                            st.caption(citation.excerpt)
                            trace = f"Chunk: {citation.chunk_id or 'unknown'}"
                            if citation.score is not None:
                                trace += f" · Relevance: {citation.score:.3f}"
                            st.caption(trace)
                st.session_state.messages.append(ChatMessage("assistant", response.answer))
            except Exception as error:
                st.error(str(error))

    st.divider()
    st.subheader("Progressive hints")
    hint_problem = st.text_area(
        "Problem or code you are working on",
        key="hint_problem",
        height=140,
    )
    if st.session_state.get("hint_problem_key") != hint_problem.strip():
        st.session_state.hint_problem_key = hint_problem.strip()
        st.session_state.hints = []
    for number, hint in enumerate(st.session_state.get("hints", []), start=1):
        st.info(f"Hint {number}: {hint}")
    if st.button("Get next hint", disabled=not hint_problem.strip()):
        try:
            progress = engine.next_hint(
                hint_problem,
                list(st.session_state.messages),
                list(st.session_state.get("hints", [])),
            )
            st.session_state.hints = list(progress.hints)
            st.rerun()
        except Exception as error:
            st.error(f"Could not generate a hint: {error}")

with runner_tab:
    st.caption(
        "Execution is disabled by default. When enabled, each run uses a disposable, "
        "resource-limited Docker container."
    )
    source = st.text_area("Python code", height=320)
    if st.button("Run code", disabled=not source.strip()):
        job = engine.start_execution(source)
        st.session_state.execution_job_id = job.id
        st.rerun()

    execution_job_id = st.session_state.get("execution_job_id")
    if execution_job_id:
        job = engine.get_execution(execution_job_id)
        if job.state in {ExecutionState.QUEUED, ExecutionState.RUNNING}:
            st.info(f"Execution is {job.state.value}.")
            refresh_column, cancel_column = st.columns(2)
            if refresh_column.button("Refresh execution status"):
                st.rerun()
            if cancel_column.button("Cancel execution"):
                engine.cancel_execution(job.id)
                st.rerun()
        elif job.error:
            st.error(job.error)
        elif job.result:
            result = job.result
            if result.cancelled:
                st.warning("Execution was cancelled.")
            if result.timed_out:
                st.warning("Execution reached its deadline.")
            if result.resource_limit_exceeded:
                st.warning("Execution reached a resource limit.")
            if result.disabled_reason:
                st.warning(result.disabled_reason)
            else:
                st.code(result.stdout or "No output", language="text")
                if result.traceback:
                    st.markdown("**Python traceback**")
                    st.code(result.traceback, language="text")
                if result.stderr:
                    st.markdown("**Standard error**")
                    st.code(result.stderr, language="text")
                st.caption(f"Exit: {result.exit_code} · {result.duration_seconds:.3f}s")

