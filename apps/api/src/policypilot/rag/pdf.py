"""Render the fictional policy wordings to PDF and read them back.

The seed renders Markdown sources to real PDFs so that ingestion genuinely parses PDFs with page
numbers (docs/design/catalog-and-rating.md). Rendering is deterministic: fixed creation date and
metadata, so the same source always yields the same bytes.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from fpdf import FPDF
from pypdf import PdfReader

_FIXED_DATE = datetime(2026, 1, 1, tzinfo=UTC)
_ASCII_REPLACEMENTS = {
    chr(0x2014): "-",  # em dash
    chr(0x2013): "-",  # en dash
    chr(0x00B7): "-",  # middle dot
    chr(0x2019): "'",  # right single quotation mark
    chr(0x201C): '"',  # left double quotation mark
    chr(0x201D): '"',  # right double quotation mark
    chr(0x00A7): "S.",  # section sign
}


def _latin1(text: str) -> str:
    for source, target in _ASCII_REPLACEMENTS.items():
        text = text.replace(source, target)
    return text.encode("latin-1", errors="replace").decode("latin-1")


def render_markdown_pdf(markdown: str, destination: Path) -> int:
    """Render the restricted Markdown used by the seed wordings. Returns the page count."""
    pdf = FPDF(format="A4")
    pdf.set_creation_date(_FIXED_DATE)
    pdf.set_creator("PolicyPilot seed")
    pdf.set_producer("PolicyPilot seed")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_margins(20, 18, 20)
    pdf.add_page()
    width = pdf.w - pdf.l_margin - pdf.r_margin

    for raw in markdown.splitlines():
        line = raw.rstrip()
        if not line:
            pdf.ln(2)
            continue
        if line.startswith("# "):
            pdf.set_font("Helvetica", "B", 15)
            pdf.multi_cell(width, 7, _latin1(line[2:]), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1)
        elif line.startswith("## "):
            pdf.ln(2)
            pdf.set_font("Helvetica", "B", 12)
            pdf.multi_cell(width, 6, _latin1(line[3:]), new_x="LMARGIN", new_y="NEXT")
        else:
            pdf.set_font("Helvetica", "", 10)
            pdf.multi_cell(width, 5, _latin1(line), new_x="LMARGIN", new_y="NEXT")
    destination.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(destination))
    return int(pdf.page_no())


def extract_pages(path: Path) -> list[str]:
    """Text of each page, in order (1-based page numbers are index + 1)."""
    reader = PdfReader(str(path))
    return [page.extract_text() or "" for page in reader.pages]


_TITLE_VERSION = re.compile(r"Version\s+(\d{4}\.\d+)")


def document_version(markdown: str) -> str:
    match = _TITLE_VERSION.search(markdown)
    return match.group(1) if match else "1"


def document_title(markdown: str) -> str:
    for line in markdown.splitlines():
        if line.startswith("# "):
            return _latin1(line[2:].strip())
    return "Policy wording"
