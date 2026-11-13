"""Validated, bounded document extraction with source-aware chunk metadata."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from io import BytesIO, StringIO
from pathlib import Path
import re
from typing import Any
import zipfile

from .models import DocumentChunk, DocumentSource
from .youtube import YouTubeTranscript


class IngestionError(ValueError):
    """Raised when an upload is invalid, unsafe, or has no usable text."""


@dataclass(frozen=True)
class ExtractedSection:
    text: str
    location: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChunkText:
    text: str
    start: int
    end: int


class DocumentIngestor:
    SUPPORTED_SUFFIXES = {".pdf", ".txt", ".md", ".rst", ".csv", ".pptx"}
    MEDIA_TYPES = {
        ".pdf": "application/pdf",
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".rst": "text/x-rst",
        ".csv": "text/csv",
        ".pptx": (
            "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        ),
    }
    MEDIA_ALIASES = {
        ".txt": {"text/plain"},
        ".md": {"text/plain", "text/markdown", "text/x-markdown"},
        ".rst": {"text/plain", "text/x-rst", "text/prs.fallenstein.rst"},
        ".csv": {
            "text/plain",
            "text/csv",
            "application/csv",
            "application/vnd.ms-excel",
        },
        ".pdf": {"application/pdf"},
        ".pptx": {
            "application/zip",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        },
    }
    MAX_PDF_PAGES = 500
    MAX_PPTX_SLIDES = 500
    MAX_CSV_ROWS = 100_000
    MAX_CSV_COLUMNS = 200
    MAX_CSV_CELL_CHARS = 100_000
    MAX_ARCHIVE_MEMBERS = 5_000
    MAX_ARCHIVE_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
    MAX_COMPRESSION_RATIO = 250
    MAX_EXTRACTED_TEXT_CHARS = 5_000_000

    def __init__(self, chunk_size: int = 1200, chunk_overlap: int = 200) -> None:
        if chunk_size < 20:
            raise ValueError("chunk_size must be at least 20 characters")
        if chunk_overlap < 0 or chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be non-negative and smaller than chunk_size")
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def validate_upload(
        self,
        filename: str,
        data: bytes,
        declared_media_type: str | None = None,
    ) -> str:
        if not data:
            raise IngestionError("The uploaded document is empty")
        suffix = Path(filename).suffix.lower()
        if suffix not in self.SUPPORTED_SUFFIXES:
            raise IngestionError(f"Unsupported document type: {suffix or 'unknown'}")

        declared = (declared_media_type or "").split(";", 1)[0].strip().lower()
        if declared and declared != "application/octet-stream":
            if declared not in self.MEDIA_ALIASES[suffix]:
                raise IngestionError(
                    f"The declared media type {declared!r} does not match {suffix}"
                )

        if suffix == ".pdf":
            if not data.startswith(b"%PDF-"):
                raise IngestionError("The file is not a valid PDF document")
        elif suffix == ".pptx":
            self._validate_pptx_archive(data)
        else:
            self._decode_text(data, filename)
        return self.MEDIA_TYPES[suffix]

    def ingest(self, source: DocumentSource) -> list[DocumentChunk]:
        suffix = source.path.suffix.lower()
        data = self._read_source(source.path)
        self.validate_upload(source.name, data, source.media_type)
        try:
            sections = self._extract(source.path, suffix, data)
        except IngestionError:
            raise
        except Exception as error:
            raise IngestionError(
                f"Could not extract {suffix.removeprefix('.').upper()} content"
            ) from error
        return self._build_chunks(source, sections)

    def ingest_text(self, source: DocumentSource, text: str) -> list[DocumentChunk]:
        return self._build_chunks(source, [ExtractedSection(text)])

    def ingest_youtube(
        self, source: DocumentSource, transcript: YouTubeTranscript
    ) -> list[DocumentChunk]:
        sections: list[ExtractedSection] = []
        current: list[str] = []
        current_chars = 0
        start_seconds = 0.0
        end_seconds = 0.0

        for segment in transcript.segments:
            text = self._normalize_text(segment.text)
            if not text:
                continue
            if current and current_chars + len(text) + 1 > self.chunk_size:
                sections.append(
                    self._transcript_section(current, start_seconds, end_seconds, transcript)
                )
                current = []
                current_chars = 0
            if not current:
                start_seconds = segment.start_seconds
            current.append(text)
            current_chars += len(text) + 1
            end_seconds = segment.start_seconds + segment.duration_seconds

        if current:
            sections.append(
                self._transcript_section(current, start_seconds, end_seconds, transcript)
            )
        return self._build_chunks(source, sections)

    def _build_chunks(
        self, source: DocumentSource, sections: list[ExtractedSection]
    ) -> list[DocumentChunk]:
        chunks: list[DocumentChunk] = []
        extracted_characters = sum(len(section.text) for section in sections)
        if extracted_characters > self.MAX_EXTRACTED_TEXT_CHARS:
            raise IngestionError("The extracted document text exceeds the safety limit")
        for section_index, section in enumerate(sections):
            normalized_section = self._normalize_text(section.text)
            for chunk_index, chunk in enumerate(self._chunk(section.text)):
                line_start = normalized_section.count("\n", 0, chunk.start) + 1
                line_end = normalized_section.count("\n", 0, chunk.end) + 1
                location = section.location
                if not location:
                    location = (
                        f"line {line_start}"
                        if line_start == line_end
                        else f"lines {line_start}-{line_end}"
                    )
                metadata = {
                    **source.metadata,
                    **section.metadata,
                    "source_id": source.id,
                    "source": source.name,
                    "source_name": source.name,
                    "source_type": source.source_type,
                    "origin_uri": source.origin_uri,
                    "media_type": source.media_type,
                    "sha256": source.sha256,
                    "location": location,
                    "section": section_index,
                    "chunk": chunk_index,
                    "char_start": chunk.start,
                    "char_end": chunk.end,
                    "line_start": line_start,
                    "line_end": line_end,
                }
                chunks.append(
                    DocumentChunk(
                        id=f"{source.id}:{section_index}:{chunk_index}",
                        document_id=source.id,
                        text=chunk.text,
                        metadata=metadata,
                    )
                )
        if not chunks:
            raise IngestionError("The document did not contain extractable text")
        return chunks

    def _extract(
        self, path: Path, suffix: str, data: bytes
    ) -> list[ExtractedSection]:
        if suffix == ".txt":
            return [ExtractedSection(self._decode_text(data, path.name))]
        if suffix == ".md":
            return self._extract_markdown(self._decode_text(data, path.name))
        if suffix == ".rst":
            return [ExtractedSection(self._extract_rst(data), metadata={"format": "rst"})]
        if suffix == ".csv":
            return self._extract_csv(data)
        if suffix == ".pdf":
            return self._extract_pdf(path)
        if suffix == ".pptx":
            return self._extract_pptx(path)
        raise IngestionError(f"Unsupported document type: {suffix}")

    @staticmethod
    def _read_source(path: Path) -> bytes:
        try:
            return path.read_bytes()
        except OSError as error:
            raise IngestionError("The uploaded document could not be read") from error

    @staticmethod
    def _decode_text(data: bytes, filename: str) -> str:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise IngestionError(f"{filename} must use UTF-8 text encoding") from error
        if "\x00" in text:
            raise IngestionError(f"{filename} contains binary NUL bytes")
        control_count = sum(
            ord(character) < 32 and character not in "\n\r\t" for character in text
        )
        if text and control_count / len(text) > 0.01:
            raise IngestionError(f"{filename} appears to contain binary data")
        return text

    @classmethod
    def _validate_pptx_archive(cls, data: bytes) -> None:
        try:
            with zipfile.ZipFile(BytesIO(data)) as archive:
                members = archive.infolist()
                names = {member.filename for member in members}
                if "[Content_Types].xml" not in names or "ppt/presentation.xml" not in names:
                    raise IngestionError("The file is not a valid PPTX presentation")
                if len(members) > cls.MAX_ARCHIVE_MEMBERS:
                    raise IngestionError("The PPTX archive contains too many files")
                total_size = sum(member.file_size for member in members)
                total_compressed = sum(member.compress_size for member in members)
                if total_size > cls.MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                    raise IngestionError("The expanded PPTX presentation is too large")
                if total_compressed and total_size / total_compressed > cls.MAX_COMPRESSION_RATIO:
                    raise IngestionError("The PPTX archive has an unsafe compression ratio")
                for member in members:
                    path = Path(member.filename.replace("\\", "/"))
                    if member.flag_bits & 1:
                        raise IngestionError("Encrypted PPTX archives are not supported")
                    if path.is_absolute() or ".." in path.parts:
                        raise IngestionError("The PPTX archive contains an unsafe path")
        except zipfile.BadZipFile as error:
            raise IngestionError("The file is not a valid PPTX presentation") from error

    @staticmethod
    def _extract_rst(data: bytes) -> str:
        from docutils import nodes
        from docutils.core import publish_doctree

        warning_stream = StringIO()
        document = publish_doctree(
            DocumentIngestor._decode_text(data, "RST document"),
            settings_overrides={
                "file_insertion_enabled": False,
                "raw_enabled": False,
                "_disable_config": True,
                "line_length_limit": 10_000,
                "report_level": 5,
                "halt_level": 6,
                "warning_stream": warning_stream,
            },
        )
        for node_type in (nodes.system_message, nodes.raw):
            for unsafe_node in list(document.findall(node_type)):
                unsafe_node.parent.remove(unsafe_node)
        return document.astext()

    @classmethod
    def _extract_csv(cls, data: bytes) -> list[ExtractedSection]:
        text = cls._decode_text(data, "CSV document")
        sample = text[:16_384]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel

        sections: list[ExtractedSection] = []
        try:
            for row_number, row in enumerate(
                csv.reader(StringIO(text), dialect=dialect), start=1
            ):
                if row_number > cls.MAX_CSV_ROWS:
                    raise IngestionError("The CSV exceeds the row limit")
                if len(row) > cls.MAX_CSV_COLUMNS:
                    raise IngestionError(f"CSV row {row_number} exceeds the column limit")
                if any(len(cell) > cls.MAX_CSV_CELL_CHARS for cell in row):
                    raise IngestionError(f"CSV row {row_number} contains an oversized cell")
                rendered = " | ".join(cell.strip() for cell in row).strip()
                if rendered:
                    sections.append(
                        ExtractedSection(
                            rendered,
                            f"row {row_number}",
                            {"row_number": row_number, "column_count": len(row)},
                        )
                    )
        except csv.Error as error:
            raise IngestionError("The CSV document is malformed") from error
        return sections

    @classmethod
    def _extract_pdf(cls, path: Path) -> list[ExtractedSection]:
        import pdfplumber

        sections: list[ExtractedSection] = []
        try:
            with pdfplumber.open(path) as pdf:
                if len(pdf.pages) > cls.MAX_PDF_PAGES:
                    raise IngestionError("The PDF exceeds the page limit")
                pdf_metadata = {
                    f"pdf_{key.lower()}": value
                    for key, value in (pdf.metadata or {}).items()
                    if isinstance(key, str) and isinstance(value, (str, int, float, bool))
                }
                for page_number, page in enumerate(pdf.pages, start=1):
                    text = page.extract_text(layout=False) or ""
                    if text.strip():
                        sections.append(
                            ExtractedSection(
                                text,
                                f"page {page_number}",
                                {"page_number": page_number, **pdf_metadata},
                            )
                        )
        except IngestionError:
            raise
        except Exception as error:
            raise IngestionError("The PDF is malformed, encrypted, or unreadable") from error
        return sections

    @classmethod
    def _extract_pptx(cls, path: Path) -> list[ExtractedSection]:
        from pptx import Presentation

        try:
            presentation = Presentation(path)
        except Exception as error:
            raise IngestionError("The PPTX presentation is malformed or unreadable") from error
        if len(presentation.slides) > cls.MAX_PPTX_SLIDES:
            raise IngestionError("The PPTX exceeds the slide limit")

        properties = presentation.core_properties
        presentation_metadata = {
            "presentation_title": properties.title or None,
            "presentation_author": properties.author or None,
            "presentation_subject": properties.subject or None,
        }
        sections: list[ExtractedSection] = []
        for slide_number, slide in enumerate(presentation.slides, start=1):
            blocks: list[str] = []
            for shape in slide.shapes:
                if getattr(shape, "has_text_frame", False):
                    text = "\n".join(
                        paragraph.text for paragraph in shape.text_frame.paragraphs
                    ).strip()
                    if text:
                        blocks.append(text)
                if getattr(shape, "has_table", False):
                    for row in shape.table.rows:
                        rendered = " | ".join(cell.text.strip() for cell in row.cells).strip()
                        if rendered:
                            blocks.append(rendered)
            text = "\n\n".join(blocks)
            if text:
                sections.append(
                    ExtractedSection(
                        text,
                        f"slide {slide_number}",
                        {"slide_number": slide_number, **presentation_metadata},
                    )
                )
        return sections

    @staticmethod
    def _extract_markdown(text: str) -> list[ExtractedSection]:
        heading_pattern = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
        sections: list[ExtractedSection] = []
        heading_path: list[str] = []
        current_lines: list[str] = []
        current_location: str | None = None
        current_level: int | None = None
        fence: str | None = None

        def flush() -> None:
            rendered = "\n".join(current_lines).strip()
            if rendered:
                metadata: dict[str, Any] = {"format": "markdown"}
                if current_level is not None:
                    metadata["heading_level"] = current_level
                    metadata["heading"] = heading_path[current_level - 1]
                sections.append(ExtractedSection(rendered, current_location, metadata))

        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            stripped = line.lstrip()
            if stripped.startswith(("```", "~~~")):
                marker = stripped[:3]
                fence = None if fence == marker else marker if fence is None else fence
                current_lines.append(line)
                continue
            match = heading_pattern.match(line) if fence is None else None
            if match:
                flush()
                level = len(match.group(1))
                heading = match.group(2).strip()
                while len(heading_path) < level:
                    heading_path.append("")
                heading_path[level - 1 :] = [heading]
                current_lines = [line]
                current_location = " > ".join(item for item in heading_path if item)
                current_level = level
            else:
                current_lines.append(line)
        flush()
        return sections

    def _chunk(self, text: str) -> list[ChunkText]:
        normalized = self._normalize_text(text)
        if not normalized:
            return []
        chunks: list[ChunkText] = []
        start = 0
        while start < len(normalized):
            end = min(start + self.chunk_size, len(normalized))
            if end < len(normalized):
                boundaries = (
                    normalized.rfind("\n\n", start, end),
                    normalized.rfind("\n", start, end),
                    normalized.rfind(" ", start, end),
                )
                boundary = max(boundaries)
                if boundary > start + self.chunk_size // 2:
                    end = boundary
            raw_chunk = normalized[start:end]
            leading = len(raw_chunk) - len(raw_chunk.lstrip())
            trailing = len(raw_chunk.rstrip())
            chunk_start = start + leading
            chunk_end = start + trailing
            if chunk_end > chunk_start:
                chunks.append(
                    ChunkText(normalized[chunk_start:chunk_end], chunk_start, chunk_end)
                )
            if end >= len(normalized):
                break
            start = max(end - self.chunk_overlap, start + 1)
        return chunks

    @staticmethod
    def _normalize_text(text: str) -> str:
        return text.replace("\r\n", "\n").replace("\r", "\n").strip()

    @staticmethod
    def _transcript_section(
        text_parts: list[str],
        start_seconds: float,
        end_seconds: float,
        transcript: YouTubeTranscript,
    ) -> ExtractedSection:
        return ExtractedSection(
            " ".join(text_parts),
            f"{_format_timestamp(start_seconds)}-{_format_timestamp(end_seconds)}",
            {
                "video_id": transcript.video_id,
                "language": transcript.language,
                "language_code": transcript.language_code,
                "is_generated": transcript.is_generated,
                "start_seconds": start_seconds,
                "end_seconds": end_seconds,
            },
        )


def _format_timestamp(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"
