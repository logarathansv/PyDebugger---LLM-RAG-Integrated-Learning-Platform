# Document ingestion

The ingestion boundary treats filenames, declared media types, document bytes,
archive contents, markup, and remote transcript data as untrusted.

## Supported sources

- PDF: validates the PDF signature, limits page count, and indexes text per page.
- TXT: requires UTF-8 (with optional BOM) and rejects NUL or binary-like controls.
- CSV: accepts common comma, semicolon, tab, and pipe delimiters; limits rows,
  columns, and cell size; and retains row numbers.
- PPTX: validates the OPC ZIP structure, encrypted entries, member paths,
  member count, expanded size, and compression ratio before parsing. Text boxes
  and tables are indexed per slide.
- Markdown: groups content by ATX heading hierarchy while respecting fenced code.
- reStructuredText: disables configuration files, raw directives, includes, and
  file/URL insertion. Parser warnings and rejected directive bodies are removed
  from indexed text.
- YouTube: accepts only a strict 11-character video ID or supported HTTPS
  YouTube URL. Available transcript segments are grouped with language,
  generated/manual, video ID, and start/end timestamp metadata.

Every chunk includes the internal source ID, safe source name, source type,
canonical origin URI when applicable, media type, SHA-256 digest, section/chunk
indexes, line and character ranges, and a human-readable location. Format-specific
metadata adds page, slide, row, heading, or transcript timing details.

Extraction is bounded by the configured upload size and fixed parser safety
limits. A document that is empty, malformed, mislabeled, encrypted where
unsupported, binary when text is expected, archive-like but unsafe, or produces
no text is rejected before it enters the vector index.

## YouTube operational note

Transcript retrieval uses `youtube-transcript-api` behind an injectable provider
interface and bounded HTTP connection/read timeouts. It does not download video
media. Transcript availability, languages, regional restrictions, and cloud-IP
blocking are controlled by YouTube, so retrieval failure is reported without
adding a partial document.
