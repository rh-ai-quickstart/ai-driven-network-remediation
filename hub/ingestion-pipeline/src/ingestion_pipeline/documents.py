"""Markdown conversion and segmentation for vendor documentation (PDF/DOCX/Markdown).

Format-specific converters run once, at sync time, turning every supported vendor doc into a
single canonical markdown representation that gets stored in MinIO. Ingest time then only needs
one generic segmenter that splits markdown into `DocumentUnit`s by heading — no per-format
branching downstream, and adding a new input format later only requires a new converter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from io import BytesIO

from docx import Document
from pypdf import PdfReader

_ATX_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_LEADING_HASH_RE = re.compile(r"^(#{1,6})(\s)")

_DOCX_HEADING_MARKDOWN_PREFIX = {
    "Title": "#",
    "Heading 1": "#",
    "Heading 2": "##",
    "Heading 3": "###",
}


@dataclass(frozen=True)
class DocumentUnit:
    text: str
    attributes: dict[str, str | int | float | bool] = field(default_factory=dict)


def _escape_markdown_heading_lines(text: str) -> str:
    """Escape any source line that would otherwise be misread as one of our ATX headings."""
    return "\n".join(_LEADING_HASH_RE.sub(r"\\\1\2", line) for line in text.splitlines())


def pdf_to_markdown(data: bytes) -> str:
    """Render each non-empty PDF page as a markdown section: `## Page N` followed by its text."""
    reader = PdfReader(BytesIO(data))
    sections: list[str] = []
    for page_number, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if not text:
            continue
        sections.append(f"## Page {page_number}\n\n{_escape_markdown_heading_lines(text)}")
    return "\n\n".join(sections)


def docx_to_markdown(data: bytes) -> str:
    """Render a DOCX as markdown, mapping Title/Heading 1/2/3 styles to `#`/`##`/`###`."""
    document = Document(BytesIO(data))
    lines: list[str] = []
    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        style_name = paragraph.style.name if paragraph.style is not None else ""
        heading_prefix = _DOCX_HEADING_MARKDOWN_PREFIX.get(style_name)
        lines.append(f"{heading_prefix} {text}" if heading_prefix else _escape_markdown_heading_lines(text))
    return "\n\n".join(lines)


_SUPPORTED_EXTENSIONS = frozenset({".pdf", ".docx", ".md"})


def supported_extensions() -> frozenset[str]:
    return _SUPPORTED_EXTENSIONS


def _extension_of(filename: str) -> str:
    return f".{filename.rsplit('.', 1)[-1].lower()}" if "." in filename else ""


def convert_to_markdown(filename: str, data: bytes) -> str:
    """Dispatch to the right converter based on the filename extension."""
    extension = _extension_of(filename)
    if extension == ".pdf":
        return pdf_to_markdown(data)
    if extension == ".docx":
        return docx_to_markdown(data)
    if extension == ".md":
        return data.decode("utf-8")
    raise ValueError(f"Unsupported vendor document type for '{filename}'")


def markdown_object_name(filename: str) -> str:
    """Derived MinIO object name for a converted document, losslessly preserving the original name."""
    return f"{filename}.md"


def original_filename_from_markdown_object(object_name: str) -> str:
    """Reverse of `markdown_object_name`, recovering the original filename for citation purposes."""
    return object_name.removesuffix(".md")


def split_markdown_units(markdown_text: str) -> list[DocumentUnit]:
    """Split markdown into units at each ATX heading (`#`..`######`), tagged with the heading text.

    Text preceding the first heading (if any) becomes a unit with no `section` attribute.
    """
    units: list[DocumentUnit] = []
    current_heading: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        text = "\n".join(current_lines).strip()
        if not text:
            return
        attributes: dict[str, str | int | float | bool] = {"section": current_heading} if current_heading else {}
        units.append(DocumentUnit(text=text, attributes=attributes))

    for line in markdown_text.splitlines():
        match = _ATX_HEADING_RE.match(line)
        if match:
            flush()
            current_heading = match.group(2)
            current_lines = []
            continue
        current_lines.append(line)

    flush()
    return units
