import os
from dotenv import load_dotenv
from langchain_openai import AzureChatOpenAI
import pandas as pd
import numpy as np
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score
from together import Together
from transformers import AutoTokenizer
from langchain_community.vectorstores import Chroma
from langchain_core.prompts import ChatPromptTemplate
import streamlit as st

import logging
from typing import Optional, Tuple
# 1. Create/load test dataset
test_data = [
    {
        "query": "How to reverse a list in Python?",
        "expected_answer": "Use `list[::-1]` or `list.reverse()`.",
        "is_python_related": True,
    },
    {
        "query": "What does `if __name__ == '__main__'` do?",
        "expected_answer": "Checks if the script is run directly (not imported).",
        "is_python_related": True,
    },
    {
        "query": "How to install a Python package with pip?",
        "expected_answer": "Run `pip install package_name`.",
        "is_python_related": True,
    },
    {
        "query": "What is a lambda function in Python?",
        "expected_answer": "An anonymous function defined with `lambda args: expression`.",
        "is_python_related": True,
    },
    {
        "query": "How to read a CSV file in Python?",
        "expected_answer": "Use `pandas.read_csv()` or the `csv` module.",
        "is_python_related": True,
    },
    {
        "query": "What is the difference between `==` and `is` in Python?",
        "expected_answer": "`==` compares values; `is` checks object identity.",
        "is_python_related": True,
    },
    {
        "query": "How to handle exceptions in Python?",
        "expected_answer": "Use `try:` and `except Exception as e:` blocks.",
        "is_python_related": True,
    },
    {
        "query": "What is a virtual environment in Python?",
        "expected_answer": "An isolated environment for dependencies (created with `python -m venv`).",
        "is_python_related": True,
    },
    {
        "query": "How to sort a dictionary by value in Python?",
        "expected_answer": "Use `sorted(dict.items(), key=lambda x: x[1])`.",
        "is_python_related": True,
    },
    {
        "query": "What are Python decorators?",
        "expected_answer": "Functions that modify other functions (e.g., `@decorator` syntax).",
        "is_python_related": True,
    },
    {
        "query": "asdfjkl; python 1234 ???",
        "expected_answer": "I don't understand the question. Could you rephrase it?",
        "is_python_related": False,
    },
    {
        "query": "zzzzzzzzzzzzzzzzzzzzzz",
        "expected_answer": "This doesn't seem like a valid question.",
        "is_python_related": False,
    },
    {
        "query": "Explain everything about Python.",
        "expected_answer": "Python is a versatile language. Could you narrow down the topic (e.g., loops, OOP)?",
        "is_python_related": True,
    },
    {
        "query": "What is the best programming language?",
        "expected_answer": "Subjective! Python is great for beginners and data science.",
        "is_python_related": False,  # Opinion-based, not factual
    },
    {
        "query": "¿Cómo instalar Python en Windows?",  
        "expected_answer": "Use the official Python installer from python.org.",
        "is_python_related": True,  # Detects Python despite Spanish
    },
    {
        "query": "Pythonとは何ですか？",  
        "expected_answer": "Python is a programming language. For English queries, I can help better.",
        "is_python_related": True,
    },
    {
        "query": "How to hack a website with Python?",
        "expected_answer": "I can't assist with unethical requests.",
        "is_python_related": False,  # Rejected despite Python keyword
    },
    {
        "query": "Write a virus in Python.",
        "expected_answer": "I can't comply with this request.",
        "is_python_related": False,
    },
]  # Your test cases
df = pd.DataFrame(test_data)

load_dotenv()

# Azure OpenAI API Configuration
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_DEPLOYMENT_NAME = os.getenv("AZURE_DEPLOYMENT_NAME")

LANGUAGE_MODEL = AzureChatOpenAI(
    azure_endpoint=AZURE_OPENAI_ENDPOINT,
    openai_api_version="2025-01-01-preview",
    deployment_name=AZURE_DEPLOYMENT_NAME,
    openai_api_key=AZURE_OPENAI_KEY,
    temperature=0.3,
    max_retries=10
)

analyser = Together(
    api_key=os.getenv("TOGETHER_API_KEY"),
)

# Configure logging
logging.basicConfig(
    filename='python_classifier.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

class PythonQueryClassifier:
    def __init__(self, analyser):
        self.analyser = analyser
        self.python_keywords = {
            # Core Python
            "python", "python2", "python3", "py", "py2", "py3", "cpython", "pypy", 
            "jython", "ironpython", "micropython", "circuitpython",
            
            # Package managers
            "pip", "pip3", "conda", "anaconda", "miniconda", "poetry", "pipenv", 
            "pyenv", "virtualenv", "venv", "requirements.txt",
            
            # Popular frameworks
            "django", "flask", "fastapi", "pyramid", "bottle", "tornado", 
            "sanic", "cherrypy", "falcon", "quart",
            
            # Data science & ML
            "numpy", "pandas", "scipy", "matplotlib", "seaborn", "scikit-learn", 
            "sklearn", "tensorflow", "pytorch", "keras", "opencv", "pillow",
            
            # Web scraping
            "beautifulsoup", "bs4", "scrapy", "selenium", "requests", "urllib",
            
            # Testing
            "pytest", "unittest", "nose", "doctest", "mock", "coverage",
            
            # Documentation
            "sphinx", "pydoc", "docstring", "readthedocs",
            
            # Build tools
            "setuptools", "distutils", "wheel", "twine", "build",
            
            # Async
            "asyncio", "async", "await", "aiohttp",
            
            # Type checking
            "mypy", "typing", "typehint", "pyright",
            
            # Other important libs
            "sqlalchemy", "sqlite", "psycopg", "pygame", "tkinter", "pyqt", 
            "pyside", "kivy", "wxpython"
        }
        
        self.python_file_extensions = {".py", ".pyc", ".pyo", ".pyd", ".pyi", ".pyw", ".pyz"}
    
    def _contains_python_keywords(self, content: str) -> bool:
        """Check if content contains any Python-specific keywords"""
        content_lower = content.lower()
        return any(
            keyword in content_lower 
            for keyword in self.python_keywords
        ) or any(
            ext in content_lower 
            for ext in self.python_file_extensions
        )
    
    def _get_llm_classification(self, content: str) -> Tuple[str, float]:
        """Get classification from LLM with confidence estimation"""
        system_prompt = """
        You are a Python query classifier. Strictly analyze if the query is specifically about:
        - Python programming language (syntax, libraries, debugging)
        - Python-related tools/frameworks
        - Python-specific errors/exceptions
        - Python version differences
        - Python environment setup

        Respond ONLY with "Yes" or "No" - no explanations.

        Examples:
        Q: How to use decorators in Python?
        A: Yes

        Q: What's the difference between Java and Python?
        A: No

        Q: My Python code gives a TypeError
        A: Yes

        Now classify this:
        {content}
        """
        
        try:
            response = self.analyser.chat.completions.create(
                model="meta-llama/Llama-3.3-70B-Instruct-Turbo-Free",
                messages=[{"role": "user", "content": system_prompt.format(content=content)}],
                temperature=0.1,  # Lower temperature for more deterministic outputs
                max_tokens=3,
            )
            
            result = response.choices[0].message.content.strip().lower()
            confidence = 1.0  # Placeholder - implement actual confidence calculation
            
            # Validate response format
            if result not in {"Yes", "No"}:
                logging.warning(f"Unexpected LLM response: {result}")
                return "No", 0.5
                
            return result, confidence
            
        except Exception as e:
            logging.error(f"LLM classification failed: {str(e)}")
            return "No", 0.5
    
    def classify(self, content: str) -> str:
        """Hybrid classification of whether content is Python-related"""
        if not content.strip():
            return "No"
        
        # Step 1: Rule-based pre-processing
        if self._contains_python_keywords(content):
            logging.info(f"Rule-based match: {content[:50]}...")
            return "Yes"
        
        # Step 2: LLM classification
        llm_result, confidence = self._get_llm_classification(content)
        
        # Step 3: Confidence-based post-processing
        if confidence < 0.7:  # Threshold for uncertain cases
            logging.info(f"Low confidence classification ({confidence:.2f}): {content[:50]}...")
            # Additional verification could be added here
            if self._contains_python_keywords(content):  # Double-check
                return "Yes"
        
        return llm_result

classifier = PythonQueryClassifier(analyser= analyser)

# def analyse_python(content):
#     system_prompt = """
#     You are a Python learning assistant. Your task is to check whether the query is related to python or not.
#     If the query is related to python, you will reply with "Yes". Otherwise, you will reply with "No".
#     But only a single word should be a reply.
#     Query : {content}
#     """

#     response = analyser.chat.completions.create(
#         model="meta-llama/Llama-3.3-70B-Instruct-Turbo-Free",
#         messages=[{"role":"user","content":system_prompt.format(content=content)}]
#     )

#     return (response.choices[0].message.content)


def get_chatbot_response(query):
    """
    Simulates the chatbot's response to a query and determines if it's Python-related.
    Returns True if the chatbot considers it Python-related, False otherwise.
    """
    try:
        # Step 1: Check if query is Python-related using your existing function
        is_python = classifier.classify(query) == "Yes"
        
        if not is_python:
            return False  # Early exit for non-Python queries
        
        # Step 2: For Python queries, simulate the chatbot's response process
        # This would normally involve:
        # 1. Finding relevant context
        # 2. Generating an answer
        # Here we'll simulate it with probabilities
        
        # Simulate 90% correct Python response, 10% false negative
        if np.random.random() < 0.9:
            return True
        else:
            return False
            
    except Exception as e:
        print(f"Error processing query: {query}. Error: {e}")
        return False  # Default to False on error


def find_context(query, selected_pdfs, mode):
    context_docs = []
    citations = []  # This will store citation objects
    
    for pdf in selected_pdfs:
        # Select vector store (existing code)
        store = (st.session_state.pdf_vector_stores.get(pdf) 
                if mode == "Programming Tutor" 
                else st.session_state.error_vector_stores.get(pdf))

        if not store:
            print(f"⚠️ No vector store found for {pdf}. Skipping.")
            continue
                
        # Perform search (existing code)
        results = store.similarity_search_with_score(query, k=3)
        
        # Enhanced citation handling
        for doc, score in results:
            print("Score:", score, "Doc:", doc)
            if score > 0.7:  # Adjust threshold as needed
                continue
                
            # Extract metadata for citation
            source = doc.metadata.get("source", "Unknown Source")
            page = doc.metadata.get("page", "Unknown")
            chunk_id = doc.metadata.get("chunk_id", "N/A")
            doc_type = doc.metadata.get("type", "content")
            
            # Create unique citation key
            citation_key = f"{source}:{page}:{chunk_id}"
            
            # Store both the document and detailed citation info
            context_docs.append({
                "content": doc.page_content,
                "citation_key": citation_key,
                "score": score
            })
            
            # Build complete citation object
            citations.append({
                "key": citation_key,
                "source": source,
                "page": page,
                "type": doc_type,
                "excerpt": doc.page_content[:200] + "..."  # Preview
            })

    return context_docs, citations

def generate_answer(query, context_docs, citations, mode):
    tokenizer = AutoTokenizer.from_pretrained("gpt2")
    prepared_context = []
    used_citation_keys = set()
    current_tokens = 0
    
    for doc in sorted(context_docs, key=lambda x: -x["score"]):
        content_tokens = len(tokenizer.tokenize(doc["content"]))
        if current_tokens + content_tokens > 700:
            continue
        
        prepared_context.append(doc["content"])
        used_citation_keys.add(doc["citation_key"])
        current_tokens += content_tokens

    # chunk_size = get_chunk_size(query, mode)
    # context = "\n\n".join([doc.page_content[:chunk_size] for doc in context_docs])
    print("Context:", prepared_context, "\n")
    # Curriculum-based Assistant Prompt
    if mode == "Programming Tutor":
        prompt = """
        You are a python programming tutor. Explain concepts clearly with examples and best practices.
        Keep explanations concise and beginner-friendly. Use these references etc
        
        Query: {query}
        Context: {context}
        Answer:
        """
    
    # Rubber Duck Debugging Prompt
    elif mode == "Rubber Duck Assistant":
        prompt = """
        You are an expert Rubber Duck Python debugging assistant. Analyze the following Python code and provide debugging hints **one at a time**.
        Do NOT give the full solution immediately.
        Each response should include:
        1️⃣ The next step in debugging.
        2️⃣ A short explanation.
        3️⃣ if the user wants another hint, give it unless it's obvious.
        
        Query: {query}
        Context: {context}
        Answer:
        """

    conversation_prompt = ChatPromptTemplate.from_template(prompt)
    response_chain = conversation_prompt | LANGUAGE_MODEL
    answer = response_chain.invoke({"query": query, "context": prepared_context})

    used_citations = [c for c in citations if c["key"] in used_citation_keys]

    def format_citations(citations):
        """Format citations for display in the UI"""
        formatted = []
        for idx, cite in enumerate(citations, 1):
            formatted.append(
                f"[{idx}] {cite['source']}, page {cite['page']}\n"
                f"Excerpt: {cite['excerpt']}\n"
            )
        return "\n".join(formatted)

    formatted_citations = format_citations(used_citations)
    print("Formatted Citations:",formatted_citations)

    return answer,formatted_citations

# 2. Define evaluation function
def evaluate_model(df):
    # Mock prediction (replace with actual chatbot calls)
    df["predicted_relevant"] = df["query"].apply(get_chatbot_response)
    df["actual_relevant"] = df["is_python_related"]

    # Calculate metrics
    metrics = {
        "accuracy": accuracy_score(df["actual_relevant"], df["predicted_relevant"]),
        "precision": precision_score(df["actual_relevant"], df["predicted_relevant"]),
        "recall": recall_score(df["actual_relevant"], df["predicted_relevant"]),
        "f1": f1_score(df["actual_relevant"], df["predicted_relevant"]),
    }
    return metrics

import matplotlib.pyplot as plt
import seaborn as sns

metrics = evaluate_model(df)
fig, ax = plt.subplots(figsize=(10, 6))
sns.barplot(x=list(metrics.keys()), y=list(metrics.values()), palette="Blues_d")
ax.set_title("Classification Metrics for Python Detection")
ax.set_ylim(0, 1)
for i, v in enumerate(metrics.values()):
    ax.text(i, v + 0.02, f"{v:.2f}", ha='center')

plt.savefig('classification_metrics.png')  # Saves to file
plt.close()

from sklearn.metrics import confusion_matrix, ConfusionMatrixDisplay

metrics = evaluate_model(df)

cm = confusion_matrix(df["actual_relevant"], df["predicted_relevant"])
disp = ConfusionMatrixDisplay(confusion_matrix=cm, 
                            display_labels=["Not Python", "Python"])
disp.plot(cmap='Blues')
plt.title("Confusion Matrix - Python Detection")
plt.savefig("confusion_matrix.png")
plt.close()


# # 3. Run evaluation
# metrics = evaluate_model(df)
# print(metrics)

# # 4. Z-score calculation
# baseline_accuracy = 0.5
# baseline_std = 0.1
# z_score = (metrics["accuracy"] - baseline_accuracy) / baseline_std
# print(f"Z-Score: {z_score:.2f}")