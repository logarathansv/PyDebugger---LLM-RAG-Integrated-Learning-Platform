# AI providers

Set `PYDEBUGGER_AI_PROVIDER` to `gemini` or `azure_openai`. When the variable is
absent, configuration selects Gemini if `GOOGLE_API_KEY` exists and otherwise
selects Azure OpenAI.

## Gemini Developer API

```dotenv
PYDEBUGGER_AI_PROVIDER="gemini"
GOOGLE_API_KEY="<Google AI Studio API key>"
GOOGLE_CHAT_MODEL="gemini-3.5-flash-lite"
GOOGLE_EMBEDDING_MODEL="gemini-embedding-001"
PYDEBUGGER_VECTOR_COLLECTION="pydebugger-gemini-content"
```

The key uses the Gemini Developer API through `langchain-google-genai`; Vertex
AI credentials are not required. Chat response usage metadata feeds the local
monitoring store. Embeddings remain text-only through the LangChain embedding
interface.

## Azure OpenAI

```dotenv
PYDEBUGGER_AI_PROVIDER="azure_openai"
AZURE_OPENAI_ENDPOINT="https://<resource>.openai.azure.com/"
AZURE_OPENAI_API_KEY="<key>"
AZURE_CHAT_DEPLOYMENT="<chat deployment>"
AZURE_EMBEDDING_DEPLOYMENT="<embedding deployment>"
```

Provider credentials are mutually validated: Gemini does not require Azure
settings, and Azure does not require a Google key. Never place real credentials
in `.env.example`; only `.env` is ignored by Git.
