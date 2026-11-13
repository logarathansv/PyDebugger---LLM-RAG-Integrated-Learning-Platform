from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from pptx import Presentation

from pydebugger.engine import PyDebuggerEngine
from pydebugger.execution import DisabledExecutor
from pydebugger.ingestion import DocumentIngestor, IngestionError
from pydebugger.models import DocumentSource
from pydebugger.youtube import (
    TranscriptError,
    TranscriptSegment,
    YouTubeTranscript,
    parse_youtube_video_id,
)

from test_core_engine import FakeAIProvider, make_settings


def source(path: Path, media_type: str) -> DocumentSource:
    return DocumentSource(
        id="document-id",
        name=path.name,
        path=path,
        media_type=media_type,
        sha256="abc123",
        metadata={"course_id": "python-101"},
    )


def minimal_pdf(text: str) -> bytes:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode("ascii"))
        output.extend(body)
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n".encode("ascii")
    )
    return bytes(output)


def test_upload_validation_rejects_spoofed_and_binary_files():
    ingestor = DocumentIngestor()

    with pytest.raises(IngestionError, match="valid PDF"):
        ingestor.validate_upload("lesson.pdf", b"not a pdf", "application/pdf")
    with pytest.raises(IngestionError, match="NUL"):
        ingestor.validate_upload("lesson.txt", b"hello\x00world", "text/plain")
    with pytest.raises(IngestionError, match="does not match"):
        ingestor.validate_upload("lesson.csv", b"a,b\n1,2", "application/pdf")


def test_safe_filename_removes_paths_preserves_extension_and_avoids_devices():
    assert PyDebuggerEngine._safe_filename("../../CON.txt") == "_CON.txt"
    long_name = "a" * 300 + ".pdf"
    safe_name = PyDebuggerEngine._safe_filename(long_name)
    assert len(safe_name) <= 180
    assert safe_name.endswith(".pdf")


def test_pdf_extraction_preserves_page_and_source_metadata(tmp_path: Path):
    path = tmp_path / "lesson.pdf"
    path.write_bytes(minimal_pdf("Python exceptions"))

    chunks = DocumentIngestor().ingest(source(path, "application/pdf"))

    assert "Python exceptions" in chunks[0].text
    assert chunks[0].metadata["page_number"] == 1
    assert chunks[0].metadata["location"] == "page 1"
    assert chunks[0].metadata["sha256"] == "abc123"
    assert chunks[0].metadata["course_id"] == "python-101"


def test_markdown_preserves_heading_hierarchy_and_ignores_fenced_hashes(tmp_path: Path):
    path = tmp_path / "lesson.md"
    path.write_text(
        "# Functions\nIntro\n\n## Errors\nHandle exceptions.\n"
        "```python\n# not a heading\n```\n",
        encoding="utf-8",
    )

    chunks = DocumentIngestor().ingest(source(path, "text/markdown"))

    assert [chunk.metadata["location"] for chunk in chunks] == [
        "Functions",
        "Functions > Errors",
    ]
    assert chunks[1].metadata["heading"] == "Errors"
    assert "# not a heading" in chunks[1].text


def test_csv_detects_delimiter_and_preserves_row_numbers(tmp_path: Path):
    path = tmp_path / "errors.csv"
    path.write_text("error;meaning\nTypeError;wrong type\n", encoding="utf-8")

    chunks = DocumentIngestor().ingest(source(path, "text/csv"))

    assert chunks[1].text == "TypeError | wrong type"
    assert chunks[1].metadata["row_number"] == 2
    assert chunks[1].metadata["column_count"] == 2


def test_pptx_extracts_text_tables_and_slide_metadata(tmp_path: Path):
    presentation = Presentation()
    presentation.core_properties.title = "Python Course"
    presentation.core_properties.author = "Instructor"
    slide = presentation.slides.add_slide(presentation.slide_layouts[5])
    slide.shapes.title.text = "Exceptions"
    table = slide.shapes.add_table(2, 2, 0, 1_000_000, 4_000_000, 1_000_000).table
    table.cell(0, 0).text = "Error"
    table.cell(0, 1).text = "Meaning"
    table.cell(1, 0).text = "TypeError"
    table.cell(1, 1).text = "Wrong type"
    buffer = BytesIO()
    presentation.save(buffer)
    path = tmp_path / "lesson.pptx"
    path.write_bytes(buffer.getvalue())

    chunks = DocumentIngestor().ingest(
        source(
            path,
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        )
    )

    assert "Exceptions" in chunks[0].text
    assert "TypeError | Wrong type" in chunks[0].text
    assert chunks[0].metadata["slide_number"] == 1
    assert chunks[0].metadata["presentation_title"] == "Python Course"


def test_rst_removes_raw_and_external_file_directives(tmp_path: Path):
    (tmp_path / "private.txt").write_text("PRIVATE-CONTENT", encoding="utf-8")
    path = tmp_path / "lesson.rst"
    path.write_text(
        "Safe heading\n============\n\nVisible text.\n\n"
        ".. include:: private.txt\n\n"
        ".. raw:: html\n\n   <script>RAW-CONTENT</script>\n",
        encoding="utf-8",
    )

    chunks = DocumentIngestor().ingest(source(path, "text/x-rst"))
    combined = "\n".join(chunk.text for chunk in chunks)

    assert "Visible text" in combined
    assert "PRIVATE-CONTENT" not in combined
    assert "RAW-CONTENT" not in combined


@pytest.mark.parametrize(
    "reference",
    [
        "dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ",
    ],
)
def test_youtube_reference_parsing(reference: str):
    assert parse_youtube_video_id(reference) == "dQw4w9WgXcQ"


def test_youtube_reference_rejects_non_youtube_hosts():
    with pytest.raises(TranscriptError, match="Only youtube.com"):
        parse_youtube_video_id("https://example.com/watch?v=dQw4w9WgXcQ")


class FakeTranscriptProvider:
    def fetch(self, video_id: str, languages=("en",)) -> YouTubeTranscript:
        assert video_id == "dQw4w9WgXcQ"
        assert tuple(languages) == ("en", "ta")
        return YouTubeTranscript(
            video_id=video_id,
            language="English",
            language_code="en",
            is_generated=False,
            segments=(
                TranscriptSegment("Python loops repeat work.", 5.0, 3.0),
                TranscriptSegment("Break stops the loop.", 8.0, 4.0),
            ),
        )


def test_engine_indexes_youtube_transcript_with_origin_metadata(tmp_path: Path):
    ai = FakeAIProvider()
    engine = PyDebuggerEngine(
        make_settings(tmp_path),
        ai,
        ai,
        DisabledExecutor(),
        FakeTranscriptProvider(),
    )

    indexed = engine.add_youtube_transcript(
        "https://youtu.be/dQw4w9WgXcQ", ("en", "ta")
    )
    results = engine.index.search("loop", {indexed.id}, limit=5)

    assert indexed.source_type == "youtube"
    assert indexed.origin_uri == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert indexed.path.exists()
    assert results[0].chunk.metadata["video_id"] == "dQw4w9WgXcQ"
    assert results[0].chunk.metadata["start_seconds"] == 5.0
    assert results[0].chunk.metadata["location"] == "0:05-0:12"
