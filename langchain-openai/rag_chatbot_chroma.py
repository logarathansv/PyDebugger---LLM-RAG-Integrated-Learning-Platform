import os
import faiss
from langchain.schema import Document
from openai import BadRequestError
import streamlit as st
from langchain_community.document_loaders import PDFPlumberLoader
from langchain_text_splitters import MarkdownTextSplitter, RecursiveCharacterTextSplitter
from langchain_community.vectorstores import FAISS  
from langchain_core.prompts import ChatPromptTemplate
from dotenv import load_dotenv
from langchain_openai import AzureChatOpenAI, AzureOpenAIEmbeddings
import time
import json
import torch
from youtube_transcript_api import YouTubeTranscriptApi
from pptx import Presentation
from urllib.parse import urlparse, parse_qs
from sentence_transformers import SentenceTransformer
from langchain.embeddings.base import Embeddings
from typing import List
from together import Together
from docutils.core import publish_parts
from transformers import AutoTokenizer
from langchain.retrievers import ContextualCompressionRetriever
from langchain.retrievers.document_compressors import LLMChainExtractor
from diskcache import Cache

if "llm_summary_generated" not in st.session_state:
    st.session_state.llm_summary_generated = False

class CustomEmbeddings(Embeddings):
    def __init__(self, model_name: str, device: str = None):
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.model = SentenceTransformer(model_name, device="cpu")  # Initialize on CPU first
        self.model.to(self.device)  # Now move to target device
        
    def embed_documents(self, documents: List[str]) -> List[List[float]]:
        with torch.no_grad():
            return [self.model.encode(d, convert_to_tensor=True).cpu().numpy().tolist() 
                    for d in documents]
    
    def embed_query(self, query: str) -> List[float]:
        with torch.no_grad():
            return self.model.encode([query], convert_to_tensor=True)[0].cpu().numpy().tolist()

# Load environment variables
load_dotenv()
cache = Cache("~/.rag_cache")

# Azure OpenAI Embeddings Configuration
AZURE_EMBEDDING_ENDPOINT = os.getenv("AZURE_EMBEDDING_OPENAI")
AZURE_EMBEDDING_API = os.getenv("AZURE_EMBEDDING_API_KEY")
AZURE_EMBEDDING_DEPLOYMENT = os.getenv("AZURE_EMBEDDING_DEPLOYMENT")

EMBEDDING_MODEL = AzureOpenAIEmbeddings(
    model=AZURE_EMBEDDING_DEPLOYMENT,
    azure_endpoint=AZURE_EMBEDDING_ENDPOINT,
    openai_api_version="2023-05-15",
    api_key=AZURE_EMBEDDING_API,
)

analyser = Together(
    api_key=os.getenv("TOGETHER_API_KEY"),
)

EMBEDDING_MODEL_2 = CustomEmbeddings("all-MiniLM-L6-v2")

# Azure OpenAI API Configuration
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_DEPLOYMENT_NAME = os.getenv("AZURE_DEPLOYMENT_NAME")

# Initialize Azure LLM
LANGUAGE_MODEL = AzureChatOpenAI(
    azure_endpoint=AZURE_OPENAI_ENDPOINT,
    openai_api_version="2025-01-01-preview",
    deployment_name=AZURE_DEPLOYMENT_NAME,
    openai_api_key=AZURE_OPENAI_KEY,
    temperature=0.3,
    max_retries=10
)

st.set_page_config(
    page_title="PyDebug",  # Replace with your desired title
    page_icon="🔧")
# Dark Mode UI
st.markdown("""
    <style>
    .stApp {
        background-color: #FFFFFF;
        color: #000000;
    }
    .stChatInput input {
        background-color: #F9F9F9 !important;
        color: #000000 !important;
        border: 1px solid #CCCCCC !important;
    }
    .stChatMessage p, .stChatMessage div {
        color: #000000 !important;
    }
    .stFileUploader {
        background-color: #F9F9F9;
        border: 1px solid #CCCCCC;
        border-radius: 5px;
        padding: 15px;
    }
    h1, h2, h3 {
        color: #00796B !important;
    }
    .context-box {
        background-color: #FAFAFA;
        border: 1px solid #DDDDDD;
        border-radius: 5px;
        padding: 10px;
        margin-top: 10px;
        font-family: monospace;
        font-size: 0.9em;
    }
    .context-title {
        color: #00796B;
        font-weight: bold;
        margin-bottom: 5px;
    }

    /* Sidebar light styling */
    section[data-testid="stSidebar"] {
        background-color: #F7F7F7;
    }

    /* File uploader title */
    .stFileUploader label {
        color: #000000 !important;
    }

    /* Buttons */
    button {
        background-color: #E0F2F1;
        color: #000000;
        border: 1px solid #CCCCCC;
    }

    /* Input boxes and text areas */
    textarea, input[type="text"] {
        background-color: #FFFFFF !important;
        color: #000000 !important;
        border: 1px solid #CCCCCC !important;
    }

    /* Radio buttons */
    .stRadio label {
        color: #000000 !important;
    }

    /* Checkbox text */
    .stCheckbox span {
        color: #000000 !important;
    }

    </style>
    """, unsafe_allow_html=True)


# System Config
PDF_STORAGE_PATH = 'document_store/pdfs/'
VECTOR_DB_PATH = 'vector_db/'
VECTOR_DB_PATH_ERROR = 'vector_db_ERROR/'
PDF_LIST_PATH = os.path.join(VECTOR_DB_PATH, "pdf_list.json")

# Ensure directories exist
os.makedirs(PDF_STORAGE_PATH, exist_ok=True)
os.makedirs(VECTOR_DB_PATH, exist_ok=True)

def format_time(seconds):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"

def save_transcript(video_name, transcript_text):
    file_path = os.path.join(PDF_STORAGE_PATH, f"{video_name}.txt")
    with open(file_path, "w", encoding="utf-8") as file:
        file.write(transcript_text)
    return file_path

def extract_video_id(url):
    parsed_url = urlparse(url)
    video_id = None
    
    if parsed_url.netloc in ["www.youtube.com", "youtube.com", "m.youtube.com"]:
        video_id = parse_qs(parsed_url.query).get("v", [None])[0]
    elif parsed_url.netloc in ["youtu.be"]:  # Shortened URL format
        video_id = parsed_url.path.lstrip("/")

    return video_id

def parse_rst(file_path):
    with open(file_path, "r") as f:
        rst_content = f.read()
    parts = publish_parts(rst_content, writer_name="html")
    return parts["body"] 

def get_english_transcript(video_id, video_name, segment_duration=30):
    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        transcript = transcript_list.find_transcript(['en']).fetch()
        
        segments = []
        current_segment = []
        start_time = 0

        for entry in transcript:
            if entry["start"] - start_time >= segment_duration:
                segments.append(f"[{format_time(start_time)}] " + " ".join(current_segment))
                start_time = entry["start"]
                current_segment = []
            
            current_segment.append(entry["text"])

        if current_segment:
            segments.append(f"[{format_time(start_time)}] " + " ".join(current_segment))

        file_path = save_transcript(video_name, "\n".join(segments))
        return file_path, "\n".join(segments)

    except Exception as e:
        return None, f"Error: {e}"

def save_pdf_list(pdf_list):
    with open(PDF_LIST_PATH, "w") as f:
        json.dump(pdf_list, f)

def load_pdf_list():
    if os.path.exists(PDF_LIST_PATH):
        with open(PDF_LIST_PATH, "r") as f:
            return json.load(f)
    return []

def analyse_python(content):
    system_prompt = """
    You are a Python learning assistant. Your task is to check whether the query is related to python or not.
    If the query is related to python, you will reply with "Yes". Otherwise, you will reply with "No".
    But only a single word should be a reply.
    Query : {content}
    """

    response = analyser.chat.completions.create(
        model="meta-llama/Llama-3.3-70B-Instruct-Turbo-Free",
        messages=[{"role":"user","content":system_prompt.format(content=content)}]
    )

    return (response.choices[0].message.content)

# Initialize Session State
if "pdf_vector_stores" not in st.session_state:
    st.session_state.pdf_vector_stores = {}

if "error_vector_stores" not in st.session_state:
    st.session_state.error_vector_stores = {}

if "pdf_list" not in st.session_state:
    st.session_state.pdf_list = load_pdf_list()

if "messages" not in st.session_state:
    st.session_state.messages = []

if "mode" not in st.session_state:
    st.session_state.mode = "Rubber Duck Assistant"

# Load existing FAISS vector stores
for pdf_name in st.session_state.pdf_list:
    try:
        vector_store = FAISS.load_local(
            os.path.join(VECTOR_DB_PATH, pdf_name.replace(" ", "_").lower()),
            EMBEDDING_MODEL,
            allow_dangerous_deserialization=True
        )
        st.session_state.pdf_vector_stores[pdf_name] = vector_store
        
        vector_store_error = FAISS.load_local(
            os.path.join(VECTOR_DB_PATH_ERROR, pdf_name.replace(" ", "_").lower()),
            EMBEDDING_MODEL_2,
            allow_dangerous_deserialization=True
        )
        st.session_state.error_vector_stores[pdf_name] = vector_store_error
    except:
        pass

def generate_follow_up_hint(chat_history, mode):
    # Extract the conversation context
    print(chat_history)
    conversation_context = "\n".join([f"{msg[0]}: {msg[1]}" for msg in chat_history])
    conversation_context = conversation_context[:200]
    print(conversation_context)
    # Define the follow-up hint prompt
    if st.session_state.mode == "Programming Tutor":
        prompt = """
        You are a programming tutor. Provide another hint or clarification based on the conversation below.
        Keep your response concise and helpful.

        Conversation:
        {conversation_context}

        Hint:
        """
    elif st.session_state.mode == "Rubber Duck Assistant":
        prompt = """
        You are a rubber duck debugging assistant. Provide another guiding question or hint to help the user think through their problem.

        Conversation:
        {conversation_context}

        Hint:
        """

    # Generate the follow-up hint
    conversation_prompt = ChatPromptTemplate.from_template(prompt)
    response_chain = conversation_prompt | LANGUAGE_MODEL
    return response_chain.invoke({"conversation_context": conversation_context})


def extract_text_from_txt(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()

def extract_text_from_pptx(file_path):
    presentation = Presentation(file_path)
    return "\n".join([shape.text for slide in presentation.slides for shape in slide.shapes if hasattr(shape, "text")])

def save_uploaded_file(uploaded_file):
    file_path = os.path.join(PDF_STORAGE_PATH, uploaded_file.name)
    with open(file_path, "wb") as file:
        file.write(uploaded_file.getbuffer())
    return file_path

def process_document(file_name, file_path):
    file_ext = file_name.split(".")[-1].lower()
    
    if file_ext == "pdf":
        loader = PDFPlumberLoader(file_path)
        docs = loader.load()
    elif file_ext == "txt":
        text = extract_text_from_txt(file_path)
        docs = [Document(page_content=text)]
    elif file_ext == "pptx":
        text = extract_text_from_pptx(file_path)
        docs = [Document(page_content=text)]
    elif file_ext == "csv":
        text = extract_text_from_csv(file_path)
        docs = text
    elif file_ext == "rst" or file_ext == "md":
        if file_ext == "rst":
            plain_text = parse_rst(file_path)
            docs = [Document(page_content=plain_text, metadata={"source": file_name})]
        else:
            with open(file_path, "r", encoding="utf-8") as f:
                markdown_content = f.read()
            docs = [Document(page_content=markdown_content, metadata={"source": file_name})]
    else:
        st.error("Unsupported file format.")
        return
    
    if file_ext == "md" or file_ext == "rst":
        chunker = MarkdownTextSplitter(chunk_size=500, chunk_overlap=100)
        chunks = chunker.split_documents(docs)

        documents = [
            Document(
                page_content=chunk.page_content,
                metadata={"source": file_name, "chunk_id": i}
            )
            for i, chunk in enumerate(chunks)
        ]

        vector_store = FAISS.from_documents(
            documents=documents,
            embedding=EMBEDDING_MODEL_2,
            metric_type=faiss.METRIC_INNER_PRODUCT
        )
        vector_store.save_local(os.path.join(VECTOR_DB_PATH_ERROR, file_name.replace(" ", "_").lower()))
        st.session_state.error_vector_stores[file_name] = vector_store
    else:
        chunker = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=200)
        chunks = chunker.split_documents(docs)
        
        documents = [
            Document(
                page_content=chunk.page_content,
                metadata={"source": file_name, "chunk_id": i}
            )
            for i, chunk in enumerate(chunks)
        ]

        print(documents)

        vector_store = FAISS.from_documents(
            documents=documents,
            embedding=EMBEDDING_MODEL
        )
        vector_store.save_local(os.path.join(VECTOR_DB_PATH, file_name.replace(" ", "_").lower()))
        st.session_state.pdf_vector_stores[file_name] = vector_store

    if file_name not in st.session_state.pdf_list:
        st.session_state.pdf_list.append(file_name)
        save_pdf_list(st.session_state.pdf_list)

def extract_text_from_csv(file_path):
    import pandas as pd
    df = pd.read_csv(file_path)
    text = "\n".join(df.astype(str).apply(lambda row: ", ".join(row), axis=1))
    return text

@cache.memoize()
def find_context(query, selected_pdfs, mode):
    context_docs = []
    citations = []
    
    for pdf in selected_pdfs:
        store = (st.session_state.pdf_vector_stores.get(pdf) 
                if mode == "Programming Tutor" 
                else st.session_state.error_vector_stores.get(pdf))

        if not store:
            continue
                
        # Create a retriever with compression for better results
        compressor = LLMChainExtractor.from_llm(analyser)
        compression_retriever = ContextualCompressionRetriever(
            base_compressor=compressor,
            base_retriever=store.as_retriever(search_kwargs={"k": 3},
            )
        )        
        # Get compressed documents
        compressed_docs = compression_retriever.get_relevant_documents(query)
        
        for doc in compressed_docs:
            source = doc.metadata.get("source", "Unknown Source")
            chunk_id = doc.metadata.get("chunk_id", "N/A")
            
            citation_key = f"{source}:{chunk_id}"
            
            context_docs.append({
                "content": doc.page_content,
                "citation_key": citation_key,
                "source": source
            })
            
            citations.append({
                "key": citation_key,
                "source": source,
                "excerpt": doc.page_content[:200] + "..."
            })

    return context_docs, citations

@cache.memoize()
def generate_answer(query, context_docs, citations, mode):
    # Prepare context with clear source attribution
    context_with_sources = []
    for doc in context_docs[:3]:  # Limit to top 3 most relevant contexts
        context_with_sources.append(
            f"SOURCE: {doc['source']}\nCONTENT:\n{doc['content'][:1000]}\n"
        )
    
    context = "\n".join(context_with_sources)

    if mode == "Programming Tutor":
        prompt = """
        You are a python programming tutor. Explain concepts clearly with examples and best practices.
        Always reference the provided context when available. If the answer isn't in the context, say so.
        
        Context:
        {context}
        
        Question: {query}
        
        Answer in this format:
        1. [Explanation]
        2. [Example if applicable]
        3. [Best practices]
        4. [Sources from context]
        """
    elif mode == "Rubber Duck Assistant":
        prompt = """
        You are a Rubber Duck debugging assistant. Help the user think through their problem step by step.
        Use the context when relevant, but focus on guiding the user to find their own solution.
        
        Context:
        {context}
        
        Problem: {query}
        
        Respond with:
        1. [First debugging step/question]
        2. [What to look for]
        3. [Potential solutions from context if available]
        """

    conversation_prompt = ChatPromptTemplate.from_template(prompt)
    response_chain = conversation_prompt | LANGUAGE_MODEL
    answer = response_chain.invoke({"query": query, "context": context})

    # Format citations for display
    formatted_citations = "\n".join(
        [f"🔍 Source: {cite['source']}\n   Excerpt: {cite['excerpt']}\n" 
         for cite in citations[:3]]  # Show top 3 citations
    )

    return answer, formatted_citations, context

# Sidebar - Upload PDFs
st.sidebar.header("📤 Upload Programming Resources")
uploaded_files = st.sidebar.file_uploader("Upload PDF, TXT, PPTX, CSV", type=["pdf", "txt", "pptx", "csv", "rst", "md"], accept_multiple_files=True)

# Process New PDFs
if uploaded_files:
    for pdf in uploaded_files:
        if pdf.name not in st.session_state.pdf_vector_stores:
            file_path = save_uploaded_file(pdf)
            process_document(pdf.name, file_path)
    st.sidebar.success("✅ Documents uploaded! Select them below.")

st.title("Programming Tutor & Rubber Duck Assistant")
st.markdown("---")

st.sidebar.header("📹 YouTube Transcript Extractor")
video_url = st.sidebar.text_input("🔗 Paste YouTube link here:")

if st.sidebar.button("🎬 Get Transcript"):
    if video_url:
        try:
            video_id = extract_video_id(video_url)
            if not video_id:
                st.error("❌ Invalid YouTube URL. Please check and try again.")
                st.stop()

            video_name = f"transcript_{video_id}"
            file_path, transcript_text = get_english_transcript(video_id, video_name)

            if file_path:
                pdf_list = load_pdf_list()
                if video_name not in pdf_list:
                    pdf_list.append(video_name)
                    save_pdf_list(pdf_list)
                st.sidebar.success(f"✅ Transcript saved as: {video_name}.txt")
                st.rerun()
            else:
                st.error(transcript_text)
        except Exception as e:
            st.sidebar.error(f"❌ Error: {e}")
    else:
        st.warning("⚠ Please enter a valid YouTube link.")

# Sidebar - Select PDFs (Checkbox)
st.sidebar.header("📑 Select Files")
selected_pdfs = [pdf for pdf in st.session_state.pdf_list if st.sidebar.checkbox(pdf, value=True)]

# Sidebar - Delete Files
st.sidebar.header("🗑️ Remove Files")
delete_pdf = st.sidebar.selectbox("Select PDF to Remove", ["None"] + st.session_state.pdf_list)

if st.sidebar.button("❌ Delete Files") and delete_pdf != "None":
    try:
        # Delete the vector store files
        import shutil
        shutil.rmtree(os.path.join(VECTOR_DB_PATH, delete_pdf.replace(" ", "_").lower()))
        # shutil.rmtree(os.path.join(VECTOR_DB_PATH_ERROR, delete_pdf.replace(" ", "_").lower()))
        
        # Remove from session state
        if delete_pdf in st.session_state.pdf_vector_stores:
            del st.session_state.pdf_vector_stores[delete_pdf]
        if delete_pdf in st.session_state.error_vector_stores:
            del st.session_state.error_vector_stores[delete_pdf]
            
        st.session_state.pdf_list.remove(delete_pdf)
        save_pdf_list(st.session_state.pdf_list)
        st.sidebar.success(f"Deleted {delete_pdf}")
        st.rerun()
    except Exception as e:
        st.sidebar.error(f"Error deleting: {e}")

# Chatbot Mode Selection
mode = st.radio("Choose Assistant Mode:", ["Rubber Duck Assistant","Programming Tutor"])
st.session_state.mode = mode

# Chat Section
if selected_pdfs:
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if "context" in message:
                with st.expander("View Context Used"):
                    st.markdown(f'<div class="context-box">{message["context"]}</div>', unsafe_allow_html=True)

    user_query = st.chat_input("Ask a question about programming concepts or debugging...")

    if user_query:
        st.session_state.messages.append({"role": "user", "content": user_query})
        with st.chat_message("user"):
            st.markdown(user_query)

        with st.spinner("🔍 Searching resources..."):
            if analyse_python(user_query) == "Yes":
                relevant_docs, citations = find_context(user_query, selected_pdfs, st.session_state.mode)
                flag = True
                try:
                    ai_response, cited, context_used = generate_answer(user_query, relevant_docs, citations, st.session_state.mode)
                except BadRequestError as e:
                    flag = False
                    ai_response = "Sorry, I cannot provide a response to that query due to content filtering policies. Please rephrase your question."
            else:
                flag = False
                ai_response = "Sorry, I can only answer Python-related questions. Please ask a Python programming question."

        if flag:
            with st.chat_message("assistant"):
                st.markdown(ai_response.content)
                if cited:
                    with st.expander("References Used"):
                        st.markdown(cited)
                if context_used:
                    st.session_state.messages.append({
                        "role": "assistant", 
                        "content": ai_response.content,
                        "context": context_used
                    })
        else:
            st.markdown(ai_response)
            st.session_state.messages.append({"role": "assistant", "content": ai_response})
else:
    st.warning("⚠️ Please upload and select a document to start chatting.")
from streamlit.components.v1 import html
import json
from io import StringIO
import ast
import traceback
from contextlib import redirect_stdout, redirect_stderr


class DebugSandbox:
    def __init__(self):
        self.breakpoints = set()
        self.current_line = 0
        self.variables = {}
        self.execution_log = []
        self.execution_paused = False
        self.breakpoint_hit = False

    def execute_with_debug(self, code, step_mode=False):
        """Execute code with debug capabilities"""
        tree = ast.parse(code)
        self._instrument_code(tree)

        output = StringIO()
        error = StringIO()

        # Add self reference to the execution namespace
        self.variables['self'] = self
        self.variables['__step_mode__'] = step_mode

        try:
            ast.fix_missing_locations(tree)
            with redirect_stdout(output), redirect_stderr(error):
                exec(compile(tree, '<string>', 'exec'), self.variables)
        except Exception:
            error.write(traceback.format_exc())
        finally:
            # Remove self reference to avoid pollution
            if 'self' in self.variables:
                del self.variables['self']
            if '__step_mode__' in self.variables:
                del self.variables['__step_mode__']

        return {
            'output': output.getvalue(),
            'error': error.getvalue(),
            'variables': {k: v for k, v in self.variables.items() if not k.startswith('__')},
            'execution_log': self.execution_log
        }

    def _instrument_code(self, node):
        """Add debugging instrumentation to AST"""
        for field, old_value in ast.iter_fields(node):
            if isinstance(old_value, list):
                new_values = []
                for value in old_value:
                    if isinstance(value, ast.stmt):
                        value = self._instrument_statement(value)
                    new_values.append(value)
                setattr(node, field, new_values)

    def _instrument_statement(self, node):
        self.current_line += 1
        line_num = self.current_line
        
        # Create breakpoint check
        breakpoint_check = ast.If(
            test=ast.Call(
                func=ast.Attribute(
                    value=ast.Name(id='self', ctx=ast.Load()),
                    attr='_check_breakpoint',
                    ctx=ast.Load()
                ),
                args=[ast.Constant(value=line_num)],
                keywords=[]
            ),
            body=[ast.Pass()],
            orelse=[]
        )
        
        # Create step mode check
        step_check = ast.If(
            test=ast.Name(id='__step_mode__', ctx=ast.Load()),
            body=[
                ast.Expr(value=ast.Call(
                    func=ast.Attribute(
                        value=ast.Name(id='self', ctx=ast.Load()),
                        attr='_pause_execution',
                        ctx=ast.Load()
                    ),
                    args=[ast.Constant(value=line_num)],
                    keywords=[]
                ))
            ],
            orelse=[]
        )
        
        # Create log expression
        log_expr = ast.Expr(value=ast.Call(
            func=ast.Attribute(value=ast.Name(id='self', ctx=ast.Load()), 
            attr='_log_execution', 
            ctx=ast.Load()),
            args=[ast.Constant(value=line_num)],
            keywords=[]
        ))

        # Set location info from original node
        log_expr = ast.copy_location(log_expr, node)
        breakpoint_check = ast.copy_location(breakpoint_check, node)
        step_check = ast.copy_location(step_check, node)

        if isinstance(node, ast.FunctionDef):
            node.body = [breakpoint_check, step_check, log_expr] + node.body
            return node
        else:
            new_node = ast.If(
                test=ast.Constant(value=True),
                body=[breakpoint_check, step_check, log_expr, node],
                orelse=[]
            )
            new_node = ast.copy_location(new_node, node)
            return new_node

    def _log_execution(self, line_num):
        """Log execution details"""
        self.execution_log.append({
            'line': line_num,
            'variables': {k: v for k, v in self.variables.items() if not k.startswith('__')},
            'call_stack': traceback.extract_stack()[:-2]
        })

    def _check_breakpoint(self, line_num):
        """Check if execution should pause at this line"""
        if line_num in self.breakpoints:
            self.breakpoint_hit = True
            self.execution_paused = True
            return True
        return False

    def _pause_execution(self, line_num):
        """Pause execution for step-through debugging"""
        self.execution_paused = True
        while self.execution_paused:
            time.sleep(0.1)

    def add_breakpoint(self, line_num):
        """Add a breakpoint at the specified line number"""
        self.breakpoints.add(line_num)

    def remove_breakpoint(self, line_num):
        """Remove breakpoint from the specified line number"""
        if line_num in self.breakpoints:
            self.breakpoints.remove(line_num)

    def continue_execution(self):
        """Resume execution after pause"""
        self.execution_paused = False
        self.breakpoint_hit = False

def generate_answer1(query, code):
    # Enhanced debugging prompt
    prompt = """You are a debugging assistant. Analyze the problem and provide hint-by-hint guidance.
        Context:
        {code}
        Problem: {query}
        Provide:
        1.Give a hint
        2.Error analysis for the error and details about it
        3.Give links/reference to same issues"""

    # Assuming `llm` is a function that takes a prompt and returns a response from a language model
    answer = LANGUAGE_MODEL.invoke(prompt.format(code=code, query=query))

    return answer.content

def get_line_code(full_code, line_num):
    """Returns the specific line of code with line number, plus 2 lines of context"""
    lines = full_code.split('\n')
    start_line = max(1, line_num - 1)  # Show 1 line before (minimum line 1)
    end_line = min(len(lines), line_num + 1)  # Show 1 line after
    
    # Format with line numbers
    result = []
    for i in range(start_line, end_line + 1):
        line_content = lines[i-1]  # Lines are 0-indexed in list
        prefix = ">>" if i == line_num else f"{i:4}"  # Mark error line
        result.append(f"{prefix}: {line_content}")
    
    return '\n'.join(result)

if st.session_state.mode == "Rubber Duck Assistant":
    # Add some CSS for the debug sandbox
    st.markdown("""
        <style>
        .debug-container {
            border: 1px solid #3A3A3A;
            border-radius: 5px;
            padding: 15px;
            margin-bottom: 20px;
            background-color: #1E1E1E;
        }
        .debug-tabs .stTab {
            background-color: #1E1E1E !important;
        }
        </style>
    """, unsafe_allow_html=True)
    
    code = st.text_area("Enter Python code to debug:", height=300, key="debug_code")
    col1, col2 = st.columns([3, 1])


    with col1:
        if st.button("Run with Debugging"):
            sandbox = DebugSandbox()
            result = sandbox.execute_with_debug(st.session_state.debug_code)
            st.session_state.debug_result = result
            st.session_state.stored_code = code
            st.session_state.llm_summary_generated = False
    
    if 'debug_result' in st.session_state:
        result = st.session_state.debug_result
        code = st.session_state.stored_code
        
        st.markdown("### Execution Results")
        tab1, tab3 = st.tabs(["Output", "Errors"])
        
        with tab1:
            st.code(result['output'] or "No output", language='text')
        
        with tab3:
            if result['error']:
                st.error("**Error Details**")
                st.code(result['error'], language='python')
                
                error_lines = result['error'].strip().split('\n')
                if error_lines:
                    last_line = error_lines[-1]
                    if ':' in last_line:
                        error_type, error_message = last_line.split(':', 1)
                        error_type = error_type.strip()
                        error_message = error_message.strip()
                    else:
                        error_type = "Error"
                        error_message = last_line.strip()
                        # answer=generate_answer(error_message,code)
                    st.markdown("**Debug Summary**")
                    st.error(f"**Error Type:** `{error_type}`")
                    st.error(f"**Error Message:** `{error_message}`")
                    error_line = None
                    for line in error_lines:
                        if "line " in line.lower() and ", in " in line.lower():
                            try:
                                line_part = line.split("line ")[1]
                                line_num = line_part.split(",")[0] if "," in line_part else line_part
                                error_line = int(line_num.strip())
                            except (IndexError, ValueError):
                                continue
                    
                    if error_line is not None:
                        st.error(f"**Error Location:** Line {error_line}")
                        st.code(get_line_code(code, error_line), language='python')

                    if st.session_state.llm_summary_generated == False:
                        response_llm = generate_answer1(error_message, code)
                        st.markdown("**Debug Summary from LLM** ")
                        st.markdown(f"{response_llm}")
                        st.session_state.messages.append({"role": "assistant", "content": response_llm})
                        st.session_state.llm_summary_generated = True
                        
                    if st.session_state.mode == "Rubber Duck Assistant" and st.session_state.messages and st.session_state.messages[-1]["role"] == "assistant":
                        if st.button("Need another hint"):
                            # Generate a follow-up hint based on the conversation context
                            with st.spinner("🤔 Generating another hint..."):
                                try:
                                    follow_up_response = generate_follow_up_hint(st.session_state.messages[-1], st.session_state.mode)
                                except BadRequestError as e:
                                    follow_up_response = "Sorry, I cannot provide a response to that query due to content filtering policies. Please rephrase your question."
                                with st.chat_message("assistant"):
                                    st.markdown(follow_up_response.content)
                                    st.session_state.messages.append({"role": "assistant", "content": follow_up_response.content})

            else:
                st.success("No errors detected")