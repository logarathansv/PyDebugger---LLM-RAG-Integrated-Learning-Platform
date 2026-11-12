"""Validated document extraction and structure-aware chunking."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from io import StringIO
from pathlib import Path

from .models import DocumentChunk, DocumentSource


class IngestionError(ValueError):
    pass


@dataclass(frozen=True)
class ExtractedSection:
    text: str
    location: str | None = None


class DocumentIngestor:
    SUPPORTED_SUFFIXES = {".pdf", ".txt", ".md", ".rst", ".csv", ".pptx"}

    def __init__(self, chunk_size: int = 1200, chunk_overlap: int = 200) -> None:
        if chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def ingest(self, source: DocumentSource) -> list[DocumentChunk]:
        suffix = source.path.suffix.lower()
        if suffix not in self.SUPPORTED_SUFFIXES:
            raise IngestionError(f"Unsupported document type: {suffix or 'unknown'}")
        sections = self._extract(source.path, suffix)
        chunks: list[DocumentChunk] = []
        for section_index, section in enumerate(sections):
            for chunk_index, text in enumerate(self._chunk(section.text)):
                chunks.append(
                    DocumentChunk(
                        id=f"{source.id}:{section_index}:{chunk_index}",
                        document_id=source.id,
                        text=text,
                        metadata={
                            "source": source.name,
                            "location": section.location,
                            "section": section_index,
                            "chunk": chunk_index,
                        },
                    )
                )
        if not chunks:
            raise IngestionError("The document did not contain extractable text")
        return chunks

    def ingest_text(self, source: DocumentSource, text: str) -> list[DocumentChunk]:
        return [
            DocumentChunk(
                id=f"{source.id}:0:{index}",
                document_id=source.id,
                text=chunk,
                metadata={"source": source.name, "chunk": index},
            )
            for index, chunk in enumerate(self._chunk(text))
        ]

    def _extract(self, path: Path, suffix: str) -> list[ExtractedSection]:
        if suffix in {".txt", ".md"}:
            return [ExtractedSection(path.read_text(encoding="utf-8"))]
        if suffix == ".rst":
            return [ExtractedSection(self._extract_rst(path))]
        if suffix == ".csv":
            return [ExtractedSection(self._extract_csv(path))]
        if suffix == ".pdf":
            return self._extract_pdf(path)
        if suffix == ".pptx":
            return self._extract_pptx(path)
        raise IngestionError(f"Unsupported document type: {suffix}")

    @staticmethod
    def _extract_rst(path: Path) -> str:
        from docutils.core import publish_doctree

        document = publish_doctree(
            path.read_text(encoding="utf-8"),
            settings_overrides={
                "file_insertion_enabled": False,
                "raw_enabled": False,
                "_disable_config": True,
                "line_length_limit": 10_000,
            },
        )
        return document.astext()

    @staticmethod
    def _extract_csv(path: Path) -> str:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            output = StringIO()
            for row in csv.reader(handle):
                output.write(" | ".join(row))
                output.write("\n")
        return output.getvalue()

    @staticmethod
    def _extract_pdf(path: Path) -> list[ExtractedSection]:
        import pdfplumber

        sections: list[ExtractedSection] = []
        with pdfplumber.open(path) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""
                if text.strip():
                    sections.append(ExtractedSection(text, f"page {page_number}"))
        return sections

    @staticmethod
    def _extract_pptx(path: Path) -> list[ExtractedSection]:
        from pptx import Presentation

        sections: list[ExtractedSection] = []
        for slide_number, slide in enumerate(Presentation(path).slides, start=1):
            text = "\n".join(
                shape.text for shape in slide.shapes if hasattr(shape, "text")
            )
            if text.strip():
                sections.append(ExtractedSection(text, f"slide {slide_number}"))
        return sections

    def _chunk(self, text: str) -> list[str]:
        normalized = text.replace("\r\n", "\n").strip()
        if not normalized:
            return []
        chunks: list[str] = []
        start = 0
        while start < len(normalized):
            end = min(start + self.chunk_size, len(normalized))
            if end < len(normalized):
                boundary = max(normalized.rfind("\n", start, end), normalized.rfind(" ", start, end))
                if boundary > start + self.chunk_size // 2:
                    end = boundary
            chunk = normalized[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end >= len(normalized):
                break
            start = max(end - self.chunk_overlap, start + 1)
        return chunks

