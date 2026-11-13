"""Supported Streamlit entry point for the consolidated backend."""

from __future__ import annotations

import sys
from pathlib import Path, PurePosixPath

import streamlit as st
from dotenv import load_dotenv
from streamlit_monaco_editor import st_monaco

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
from pydebugger.models import ExecutionTestCase


def apply_theme() -> None:
    """Apply a lightweight product theme without coupling UI and engine code."""
    st.markdown(
        """
        <style>
        :root {
            --brand: #6d5dfc;
            --brand-dark: #5145cd;
            --ink: #172033;
            --muted: #657089;
            --surface: rgba(255, 255, 255, 0.78);
            --border: rgba(109, 93, 252, 0.14);
        }
        .stApp {
            background:
                radial-gradient(circle at 85% 8%, rgba(109, 93, 252, .10), transparent 24rem),
                radial-gradient(circle at 12% 72%, rgba(28, 184, 152, .08), transparent 22rem),
                #f7f8fc;
        }
        [data-testid="stHeader"] { background: transparent; }
        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, #17162b 0%, #22203e 100%);
            border-right: 1px solid rgba(255,255,255,.08);
        }
        [data-testid="stSidebar"] * { color: #f7f7ff; }
        [data-testid="stSidebar"] input,
        [data-testid="stSidebar"] textarea,
        [data-testid="stSidebar"] button,
        [data-testid="stSidebar"] button * ,
        [data-testid="stSidebar"] [data-baseweb="select"] * {
            color: #172033 !important;
        }
        [data-testid="stSidebar"] [data-testid="stAlert"] *,
        [data-testid="stSidebar"] [data-testid="stFileUploaderFile"] *,
        [data-testid="stSidebar"] [data-testid="stFileUploaderFileName"] * {
            color: #172033 !important;
        }
        [data-testid="stSidebar"] [data-testid="stAlert"] {
            border-radius: .75rem;
        }
        [data-testid="stSidebar"] .stCaptionContainer,
        [data-testid="stSidebar"] small { color: #b8b6cf !important; }
        [data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] {
            background: rgba(255,255,255,.07);
            border-color: rgba(255,255,255,.18);
        }
        .block-container { max-width: 1180px; padding-top: 2.2rem; }
        [data-testid="stMain"] [data-testid="stWidgetLabel"] p,
        [data-testid="stMain"] .stRadio label p,
        [data-testid="stMain"] .stCaptionContainer p { color: var(--ink) !important; }
        .pd-brand { display:flex; gap:.75rem; align-items:center; margin-bottom:1.5rem; }
        .pd-brand-mark {
            display:grid; place-items:center; width:2.6rem; height:2.6rem;
            border-radius:.85rem; background:linear-gradient(135deg,#8b7fff,#5c4ee5);
            box-shadow:0 10px 28px rgba(109,93,252,.35); font-size:1.25rem;
        }
        .pd-brand strong { display:block; font-size:1.05rem; }
        .pd-brand span { color:#b8b6cf; font-size:.76rem; }
        .pd-hero {
            position:relative; overflow:hidden; padding:1.7rem 1.9rem; margin-bottom:1.15rem;
            border:1px solid var(--border); border-radius:1.35rem;
            background:linear-gradient(135deg,rgba(255,255,255,.94),rgba(247,246,255,.85));
            box-shadow:0 14px 45px rgba(42,38,83,.08);
        }
        .pd-hero::after {
            content:"</>"; position:absolute; right:1.6rem; top:.15rem;
            color:rgba(109,93,252,.08); font:700 6rem/1 monospace;
        }
        .pd-eyebrow { color:var(--brand-dark); font-weight:700; font-size:.75rem; letter-spacing:.1em; text-transform:uppercase; }
        .pd-hero h1 { color:var(--ink); font-size:2rem; margin:.35rem 0 .25rem; letter-spacing:-.035em; }
        .pd-hero p { color:var(--muted); margin:0; max-width:44rem; font-size:.98rem; }
        .pd-pills { display:flex; gap:.5rem; flex-wrap:wrap; margin-top:1rem; }
        .pd-pill { padding:.32rem .65rem; border-radius:999px; background:#efedff; color:#5145a8; font-size:.75rem; font-weight:650; }
        .pd-empty {
            text-align:center; padding:2.5rem 1.4rem; border:1px dashed rgba(109,93,252,.26);
            border-radius:1.15rem; background:rgba(255,255,255,.52); margin:.5rem 0 1.1rem;
        }
        .pd-empty-icon { font-size:2rem; }
        .pd-empty h3 { color:var(--ink); margin:.55rem 0 .2rem; }
        .pd-empty p { color:var(--muted); margin:0 auto; max-width:34rem; }
        div[data-testid="stVerticalBlockBorderWrapper"] {
            background:var(--surface); border-color:var(--border) !important;
            box-shadow:0 8px 30px rgba(42,38,83,.045); border-radius:1rem;
        }
        .stTabs [data-baseweb="tab-list"] { gap:.35rem; background:#ececf5; padding:.3rem; border-radius:.8rem; }
        .stTabs [data-baseweb="tab"] { color:var(--ink) !important; border-radius:.6rem; padding:.35rem .9rem; }
        .stTabs [aria-selected="true"] { background:white; box-shadow:0 2px 9px rgba(31,29,61,.08); }
        .stButton > button[kind="primary"] { background:linear-gradient(135deg,var(--brand),var(--brand-dark)); border:0; }
        [data-testid="stChatMessage"] { background:rgba(255,255,255,.58); border:1px solid rgba(31,29,61,.06); border-radius:1rem; padding:.65rem .8rem; }
        [data-testid="stMetric"] { background:white; border:1px solid var(--border); padding:1rem; border-radius:1rem; }
        .pd-section-note { color:var(--muted); font-size:.85rem; margin-top:-.45rem; margin-bottom:.8rem; }
        @media (max-width: 700px) {
            .block-container { padding-top:1rem; }
            .pd-hero { padding:1.25rem; }
            .pd-hero h1 { font-size:1.6rem; }
            .pd-hero::after { display:none; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def get_engine() -> PyDebuggerEngine:
    if "engine" not in st.session_state:
        load_dotenv(PROJECT_ROOT / ".env")
        st.session_state.engine = PyDebuggerEngine(Settings.from_env(PROJECT_ROOT))
    return st.session_state.engine


def clear_runner() -> None:
    st.session_state.runner_files = {
        "main.py": 'def greet(name):\n    return f"Hello, {name}!"\n\nprint(greet("Python learner"))\n'
    }
    st.session_state.runner_active_file = "main.py"
    st.session_state.runner_entrypoint = "main.py"
    st.session_state.runner_tests = []
    st.session_state.runner_editor_revision = (
        st.session_state.get("runner_editor_revision", 0) + 1
    )
    st.session_state.pop("runner_tests_editor", None)
    st.session_state.pop("execution_job_id", None)


def initialize_runner() -> None:
    if "runner_files" not in st.session_state:
        clear_runner()
    files = st.session_state.runner_files
    if st.session_state.get("runner_active_file") not in files:
        st.session_state.runner_active_file = next(iter(files))
    if st.session_state.get("runner_entrypoint") not in files:
        st.session_state.runner_entrypoint = next(iter(files))


def add_runner_file() -> None:
    name = st.session_state.get("runner_new_file", "").strip().replace("\\", "/")
    path = PurePosixPath(name)
    valid = (
        bool(name)
        and not path.is_absolute()
        and path.suffix == ".py"
        and all(
            part not in {"", ".", ".."}
            and (part[:-3] if index == len(path.parts) - 1 else part).isidentifier()
            for index, part in enumerate(path.parts)
        )
    )
    if not valid:
        st.session_state.runner_file_error = (
            "Use a safe Python path such as helpers.py or package/utils.py."
        )
        return
    normalized = path.as_posix()
    if normalized in st.session_state.runner_files:
        st.session_state.runner_file_error = "That file already exists."
        return
    st.session_state.runner_files[normalized] = ""
    st.session_state.runner_active_file = normalized
    st.session_state.runner_new_file = ""
    st.session_state.runner_file_error = ""


def remove_runner_file() -> None:
    files = st.session_state.runner_files
    if len(files) <= 1:
        st.session_state.runner_file_error = "A project must contain at least one file."
        return
    active = st.session_state.runner_active_file
    del files[active]
    st.session_state.runner_active_file = next(iter(files))
    if st.session_state.get("runner_entrypoint") == active:
        st.session_state.runner_entrypoint = next(iter(files))
    st.session_state.runner_file_error = ""


st.set_page_config(
    page_title="PyDebugger · Learn Python",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded",
)
apply_theme()

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
if "ingestion_job_ids" not in st.session_state:
    st.session_state.ingestion_job_ids = []

with st.sidebar:
    st.markdown(
        """
        <div class="pd-brand">
            <div class="pd-brand-mark">⌁</div>
            <div><strong>PyDebugger</strong><span>AI learning workspace</span></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.subheader("📚 Course library")
    st.caption("Add trusted material to ground tutor answers.")
    uploads = st.file_uploader(
        "Upload learning material",
        type=["pdf", "txt", "md", "rst", "csv", "pptx"],
        accept_multiple_files=True,
    )
    if uploads and st.button("Index uploaded files", type="primary", width="stretch"):
        for upload in uploads:
            try:
                job = engine.start_document_ingestion(
                    upload.name, upload.getvalue(), upload.type
                )
                st.session_state.ingestion_job_ids.append(job.id)
                st.success(f"Queued {upload.name}")
            except IngestionError as error:
                st.error(f"{upload.name}: {error}")
            except Exception as error:
                st.error(f"Could not queue {upload.name}: {error}")

    ingestion_jobs = []
    for job_id in list(st.session_state.ingestion_job_ids):
        try:
            job = engine.get_ingestion(job_id)
        except KeyError:
            st.session_state.ingestion_job_ids.remove(job_id)
            continue
        ingestion_jobs.append(job)

    active_ingestion = any(
        job.state
        in {
            ExecutionState.QUEUED,
            ExecutionState.RUNNING,
            ExecutionState.RETRYING,
        }
        for job in ingestion_jobs
    )
    if ingestion_jobs:
        with st.expander(
            f"Ingestion activity · {len(ingestion_jobs)}",
            expanded=active_ingestion,
        ):
            for job in ingestion_jobs:
                st.caption(
                    f"{job.message} · attempt {job.attempt}/{job.max_attempts}"
                    if job.attempt
                    else job.message
                )
                st.progress(job.progress)
                if job.state in {
                    ExecutionState.QUEUED,
                    ExecutionState.RUNNING,
                    ExecutionState.RETRYING,
                }:
                    if st.button("Cancel", key=f"cancel-ingestion-{job.id}"):
                        engine.cancel_ingestion(job.id)
                        st.rerun()
                elif job.state is ExecutionState.COMPLETED and job.result:
                    st.success(f"Indexed {job.result.name}")
                elif job.state is ExecutionState.CANCELLED:
                    st.warning(job.error or job.message)
                else:
                    code = f" ({job.error_code})" if job.error_code else ""
                    st.error(f"{job.error or job.message}{code}")

            activity_refresh, activity_clear = st.columns(2)
            if active_ingestion and activity_refresh.button("Refresh", width="stretch"):
                st.rerun()
            terminal_ids = {
                job.id
                for job in ingestion_jobs
                if job.state
                not in {
                    ExecutionState.QUEUED,
                    ExecutionState.RUNNING,
                    ExecutionState.RETRYING,
                }
            }
            if terminal_ids and activity_clear.button(
                "Clear finished", width="stretch"
            ):
                st.session_state.ingestion_job_ids = [
                    job_id
                    for job_id in st.session_state.ingestion_job_ids
                    if job_id not in terminal_ids
                ]
                st.rerun()

    st.divider()
    st.subheader("▶️ Video lesson")
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
            job = engine.start_youtube_ingestion(youtube_reference, languages)
            st.session_state.ingestion_job_ids.append(job.id)
            st.success("Queued YouTube transcript")
        except IngestionError as error:
            st.error(str(error))
        except Exception as error:
            st.error(f"Could not index the YouTube transcript: {error}")

    st.divider()
    documents = engine.list_documents()
    st.subheader(f"Indexed resources · {len(documents)}")
    selected_ids = {
        document.id
        for document in documents
        if st.checkbox(document.name, value=True, key=f"document-{document.id}")
    }
    if documents:
        delete_id = st.selectbox(
            "Remove a document",
            options=[""] + [document.id for document in documents],
            format_func=lambda value: (
                "Select…"
                if not value
                else next(item.name for item in documents if item.id == value)
            ),
        )
        if delete_id and st.button("Remove selected document"):
            engine.remove_document(delete_id)
            st.rerun()
    elif not documents:
        st.caption("No resources indexed yet. Upload a document or add a video lesson.")

st.markdown(
    f"""
    <section class="pd-hero">
        <div class="pd-eyebrow">Your AI Python classroom</div>
        <h1>Learn by asking, testing, and debugging.</h1>
        <p>Get grounded explanations from your course material, progressive hints, and safe code execution in one focused workspace.</p>
        <div class="pd-pills">
            <span class="pd-pill">● Tutor ready</span>
            <span class="pd-pill">{len(documents)} resources</span>
            <span class="pd-pill">Evidence-backed answers</span>
        </div>
    </section>
    """,
    unsafe_allow_html=True,
)

mode_col, action_col = st.columns([4, 1])
with mode_col:
    mode = AssistantMode(
        st.radio(
            "Learning style",
            [item.value for item in AssistantMode],
            horizontal=True,
            help="Tutor explains concepts directly. Rubber Duck asks questions that help you discover the answer.",
        )
    )
with action_col:
    st.write("")
    if st.button("Clear conversation", width="stretch"):
        st.session_state.messages = []
        st.session_state.hints = []
        st.rerun()

chat_tab, runner_tab, monitoring_tab = st.tabs(
    ["💬 Tutor", "▶ Python lab", "◫ System health"]
)

with chat_tab:
    if not st.session_state.messages:
        st.markdown(
            """
            <div class="pd-empty">
                <div class="pd-empty-icon">💡</div>
                <h3>What would you like to understand?</h3>
                <p>Ask about a concept, paste an error, or request an explanation from your selected course resources.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
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
                            location = (
                                f" — {citation.location}" if citation.location else ""
                            )
                            source_label = citation.source_name
                            if citation.source_uri:
                                source_label = (
                                    f"[{source_label}]({citation.source_uri})"
                                )
                            st.markdown(f"**{source_label}{location}**")
                            st.caption(citation.excerpt)
                            trace = f"Chunk: {citation.chunk_id or 'unknown'}"
                            if citation.score is not None:
                                trace += f" · Relevance: {citation.score:.3f}"
                            st.caption(trace)
                st.session_state.messages.append(
                    ChatMessage("assistant", response.answer)
                )
            except Exception as error:
                st.error(str(error))

    with st.expander("🪜 Need a hint instead?", expanded=False):
        st.markdown(
            '<p class="pd-section-note">Reveal one useful nudge at a time—without giving away the complete solution.</p>',
            unsafe_allow_html=True,
        )
        hint_problem = st.text_area(
            "Problem or code you are working on",
            key="hint_problem",
            height=140,
            placeholder="Describe the problem, paste your code, and say where you are stuck…",
        )
        if st.session_state.get("hint_problem_key") != hint_problem.strip():
            st.session_state.hint_problem_key = hint_problem.strip()
            st.session_state.hints = []
        for number, hint in enumerate(st.session_state.get("hints", []), start=1):
            st.info(f"Hint {number}: {hint}")
        if st.button(
            "Reveal next hint",
            disabled=not hint_problem.strip(),
            type="primary",
        ):
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
    initialize_runner()
    st.subheader("Python lab")
    st.markdown(
        '<p class="pd-section-note">A multi-file Python workspace running only inside the isolated Docker sandbox.</p>',
        unsafe_allow_html=True,
    )

    editor_column, output_column = st.columns([3, 2], gap="large")
    with editor_column:
        file_action, file_name, add_file, remove_file = st.columns([1.2, 2.3, 1, 1])
        file_action.markdown("**Project files**")
        file_name.text_input(
            "New Python file",
            placeholder="helpers.py",
            label_visibility="collapsed",
            key="runner_new_file",
        )
        add_file.button("＋ Add", on_click=add_runner_file, width="stretch")
        remove_file.button(
            "Remove",
            on_click=remove_runner_file,
            disabled=len(st.session_state.runner_files) <= 1,
            width="stretch",
        )
        if st.session_state.get("runner_file_error"):
            st.error(st.session_state.runner_file_error)

        active_file = st.segmented_control(
            "Open file",
            options=list(st.session_state.runner_files),
            key="runner_active_file",
            label_visibility="collapsed",
        )
        active_file = active_file or next(iter(st.session_state.runner_files))
        source = st_monaco(
            value=st.session_state.runner_files[active_file],
            height="440px",
            language="python",
            lineNumbers=True,
            minimap=False,
            theme="streamlit",
            key=(f"monaco-{st.session_state.runner_editor_revision}-{active_file}"),
        )
        if isinstance(source, str):
            st.session_state.runner_files[active_file] = source

        with st.expander("Test cases", expanded=False):
            st.caption(
                "Each test runs the entry point in a fresh sandbox. Input is sent to stdin; "
                "output is compared after normalizing line endings and trailing whitespace."
            )
            test_rows = st.data_editor(
                st.session_state.runner_tests,
                num_rows="dynamic",
                width="stretch",
                hide_index=True,
                column_config={
                    "name": st.column_config.TextColumn("Name", required=True),
                    "stdin": st.column_config.TextColumn("Input (stdin)"),
                    "expected_output": st.column_config.TextColumn(
                        "Expected output", required=True
                    ),
                },
                key="runner_tests_editor",
            )
            st.session_state.runner_tests = test_rows

    with output_column:
        controls = st.container(border=True)
        with controls:
            st.markdown("**Run configuration**")
            st.selectbox(
                "Entry point",
                options=list(st.session_state.runner_files),
                key="runner_entrypoint",
            )
            run_tests = st.toggle(
                "Run configured tests",
                value=bool(st.session_state.runner_tests),
                disabled=not st.session_state.runner_tests,
            )
            run_col, reset_col = st.columns(2)
            run_clicked = run_col.button(
                "▶ Run project",
                type="primary",
                width="stretch",
                disabled=not any(st.session_state.runner_files.values()),
            )
            reset_col.button("Reset", width="stretch", on_click=clear_runner)
            st.caption(
                "Network blocked · read-only project · bounded CPU, memory and time"
            )

        if run_clicked:
            try:
                tests = [
                    ExecutionTestCase(
                        name=str(row.get("name") or f"Test {number}"),
                        stdin=str(row.get("stdin") or ""),
                        expected_output=str(row.get("expected_output") or ""),
                    )
                    for number, row in enumerate(st.session_state.runner_tests, start=1)
                    if run_tests
                ]
                job = engine.start_project_execution(
                    dict(st.session_state.runner_files),
                    st.session_state.runner_entrypoint,
                    tests,
                )
                st.session_state.execution_job_id = job.id
                st.rerun()
            except Exception as error:
                st.error(f"Could not queue execution: {error}")

        st.markdown("**Output**")
        execution_job_id = st.session_state.get("execution_job_id")
        if not execution_job_id:
            st.code("Run the project to see output here.", language="text")
        else:
            job = engine.get_execution(execution_job_id)
            active_states = {
                ExecutionState.QUEUED,
                ExecutionState.RUNNING,
                ExecutionState.RETRYING,
            }
            if job.state in active_states:
                st.info(job.message)
                st.progress(job.progress)
                refresh_column, cancel_column = st.columns(2)
                if refresh_column.button("Refresh", width="stretch"):
                    st.rerun()
                if cancel_column.button("■ Stop", width="stretch"):
                    engine.cancel_execution(job.id)
                    st.rerun()
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

                if result.test_results:
                    passed = sum(item.passed for item in result.test_results)
                    st.metric(
                        "Tests passed",
                        f"{passed}/{len(result.test_results)}",
                    )
                    for test in result.test_results:
                        icon = "✅" if test.passed else "❌"
                        with st.expander(
                            f"{icon} {test.name} · {test.duration_seconds:.3f}s",
                            expanded=not test.passed,
                        ):
                            expected_col, actual_col = st.columns(2)
                            expected_col.code(
                                test.expected_output or "<empty>", language="text"
                            )
                            actual_col.code(
                                test.actual_output or "<empty>", language="text"
                            )
                            if test.stderr:
                                st.code(test.stderr, language="text")

                if result.diagnostic:
                    diagnostic = result.diagnostic
                    st.error(f"{diagnostic.exception_type}: {diagnostic.message}")
                    location_col, line_col = st.columns(2)
                    location_col.metric("File", diagnostic.file_name)
                    line_col.metric("Line", diagnostic.line_number or "—")
                    if result.traceback:
                        st.code(result.traceback, language="text")
                elif not result.test_results:
                    st.code(result.stdout or "No output", language="text")
                if result.stderr and not result.diagnostic:
                    st.code(result.stderr, language="text")
                st.caption(f"Exit {result.exit_code} · {result.duration_seconds:.3f}s")
            elif job.error:
                code = f" ({job.error_code})" if job.error_code else ""
                st.error(f"{job.error}{code}")

with monitoring_tab:
    st.subheader("System health")
    st.markdown(
        '<p class="pd-section-note">Operational activity from the last 24 hours.</p>',
        unsafe_allow_html=True,
    )
    summaries = engine.monitoring_summary()
    if engine.monitor.last_error:
        st.warning(engine.monitor.last_error)
    if not summaries:
        st.info("No operations have been recorded yet.")
    else:
        total_calls = sum(item.calls for item in summaries)
        total_failures = sum(item.failures for item in summaries)
        total_cost = sum(item.cost_usd for item in summaries)
        success_rate = (
            100 * (total_calls - total_failures) / total_calls if total_calls else 100
        )
        calls_col, success_col, failures_col, cost_col = st.columns(4)
        calls_col.metric("Operations", total_calls)
        success_col.metric("Success rate", f"{success_rate:.1f}%")
        failures_col.metric("Failures", total_failures)
        if any(item.cost_configured for item in summaries):
            cost_col.metric("Tracked model cost", f"${total_cost:.6f}")
        else:
            cost_col.metric("Tracked model cost", "Not configured")
        st.dataframe(
            [
                {
                    "operation": item.operation,
                    "calls": item.calls,
                    "failures": item.failures,
                    "avg_ms": round(item.average_latency_ms, 2),
                    "p95_ms": round(item.p95_latency_ms, 2),
                    "input_tokens": item.input_tokens,
                    "output_tokens": item.output_tokens,
                    "embedding_tokens_est.": item.embedding_tokens,
                    "cost_usd": round(item.cost_usd, 8),
                }
                for item in summaries
            ],
            width="stretch",
            hide_index=True,
        )
        if not any(item.cost_configured for item in summaries):
            st.caption(
                "Set the three PYDEBUGGER_*_COST_PER_MILLION values to your "
                "Azure contract rates to calculate cost."
            )
