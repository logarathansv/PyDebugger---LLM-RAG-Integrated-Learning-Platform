# Retrieval and document lifecycle

PyDebugger supports one production retrieval path: `ChromaVectorIndex`, backed
by a persistent local Chroma collection. `InMemoryVectorIndex` is a deterministic
test double and is never selected by application configuration.

## Identity and compatibility

- A logical upload ID is derived from its normalized filename; a YouTube ID is
  derived from its canonical video ID. Replacing content therefore preserves the
  document ID.
- Every revision has a SHA-256 content hash and a content-derived index version.
- Chunk IDs contain the logical document ID and index version.
- Collection metadata records both the embedding deployment identity and chunk
  schema version. Startup refuses an incompatible collection instead of mixing
  embeddings or metadata formats.

## Replacement and deletion

Replacement writes and extracts the new file, embeds its versioned chunks, and
only then removes obsolete chunks and the previous file. If validation,
extraction, or embedding fails, the new file is cleaned up and the current
revision remains active. Uploading identical content is idempotent.

Deletion first removes the document's vector records. After that succeeds, the
catalog entry and files in that document's exact storage directory are removed.
This ordering avoids displaying a document whose evidence has disappeared.

## Search and citations

All selected document IDs are filtered in one Chroma search, producing a single
cross-document ranking. Each result retains the document ID, stable chunk ID,
source location, origin URI, and normalized relevance score. `TutorService`
filters results using `PYDEBUGGER_RETRIEVAL_MIN_SCORE`; when nothing passes, it
returns a deterministic no-evidence response without invoking the chat model.

The persistent local backend is appropriate for one classroom application
instance. A future multi-node deployment should implement `VectorIndex` with a
shared service rather than sharing the local Chroma directory between servers.
