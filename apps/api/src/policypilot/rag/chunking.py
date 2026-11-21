"""Section-aware, clause-level chunking of extracted policy PDFs.

Rules (docs/design/catalog-and-rating.md):
- split on numbered section headings ("4. Exclusions"); a chunk never crosses a section;
- inside a section, each numbered clause ("4.2 ...") starts a new chunk; short consecutive clauses
  are packed together up to `max_words`; a clause longer than `max_words` is split with
  `overlap_words` of overlap;
- record the section reference of the first clause and the page span.

Clause-level chunks keep embeddings focused on one topic and make citations precise.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_HEADING = re.compile(r"^(\d{1,2})\.\s+([A-Z][A-Za-z ,'&-]{2,60})$")
_CLAUSE = re.compile(r"^(\d{1,2}\.\d{1,2})\s+\S")


@dataclass(frozen=True)
class Chunk:
    index: int
    section_ref: str
    page_start: int
    page_end: int
    content: str

    @property
    def word_count(self) -> int:
        return len(self.content.split())


@dataclass
class _Clause:
    section: str
    ref: str
    page_start: int
    page_end: int
    words: list[str]


def _clauses(pages: list[str]) -> list[_Clause]:
    clauses: list[_Clause] = []
    section = "Preamble"
    current: _Clause | None = None
    for page_number, page_text in enumerate(pages, start=1):
        for raw in page_text.splitlines():
            line = raw.strip()
            if not line:
                continue
            heading = _HEADING.match(line)
            if heading:
                section = f"{heading.group(1)}. {heading.group(2).strip()}"
                current = None
                continue
            clause = _CLAUSE.match(line)
            if current is None or clause:
                title = section.split(". ", 1)[1] if ". " in section else section
                ref = f"{clause.group(1)} {title}" if clause else section
                current = _Clause(section, ref, page_number, page_number, [])
                clauses.append(current)
            current.words.extend(line.split())
            current.page_end = page_number
    return [c for c in clauses if c.section != "Preamble" and c.words]


def chunk_pages(pages: list[str], *, max_words: int = 110, overlap_words: int = 20) -> list[Chunk]:
    chunks: list[Chunk] = []

    def emit(ref: str, start: int, end: int, words: list[str]) -> None:
        chunks.append(Chunk(len(chunks), ref, start, end, " ".join(words)))

    pending: _Clause | None = None
    for clause in _clauses(pages):
        if len(clause.words) > max_words:
            if pending:
                emit(pending.ref, pending.page_start, pending.page_end, pending.words)
                pending = None
            step = max_words - overlap_words
            for offset in range(0, len(clause.words), step):
                window = clause.words[offset : offset + max_words]
                emit(clause.ref, clause.page_start, clause.page_end, window)
                if offset + max_words >= len(clause.words):
                    break
            continue
        if (
            pending is not None
            and pending.section == clause.section
            and len(pending.words) + len(clause.words) <= max_words
            and len(pending.words) < 40
        ):
            pending.words.extend(clause.words)
            pending.page_end = clause.page_end
            continue
        if pending:
            emit(pending.ref, pending.page_start, pending.page_end, pending.words)
        pending = _Clause(
            clause.section, clause.ref, clause.page_start, clause.page_end, list(clause.words)
        )
    if pending:
        emit(pending.ref, pending.page_start, pending.page_end, pending.words)
    return chunks


def embedding_text(document_title: str, chunk: Chunk) -> str:
    """Text that is embedded: the clause plus where it comes from (improves retrieval)."""
    product = document_title.split(" - ")[1] if " - " in document_title else document_title
    return f"{product}, {chunk.section_ref}: {chunk.content}"
