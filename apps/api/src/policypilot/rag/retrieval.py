"""Hybrid retrieval: pgvector cosine + PostgreSQL full-text, fused with reciprocal rank fusion."""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from policypilot.rag.embeddings import Embedder

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]{2,}")
RRF_K = 60


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: int
    document_id: uuid.UUID
    product_id: uuid.UUID
    product_code: str
    document_title: str
    section_ref: str
    page_start: int
    page_end: int
    content: str
    score: float

    @property
    def pages(self) -> str:
        if self.page_start == self.page_end:
            return str(self.page_start)
        return f"{self.page_start}-{self.page_end}"


# Words that appear in almost every wording; in an OR query they would swamp the rare, useful terms.
# Product filtering covers carrier and product names.
_GENERIC = frozenset(
    {
        *("insurance", "insurer", "policy", "policies", "cover", "covered", "coverage"),
        *("plan", "plans", "product", "products", "kestrel", "solace", "tollgate"),
        *("evergreen", "mutual", "cooperative", "assurance", "term", "life", "health"),
        *("auto", "car", "vehicle", "silver", "gold", "bronze", "comprehensive"),
        *("standard", "liability", "basic", "plus", "flex", "family", "choice", "saver"),
        *("complete", "does", "what", "when", "how", "can", "will", "would", "under"),
        *("with", "from", "about"),
    }
)


def _or_tsquery(query: str) -> str:
    # Only alphanumeric words reach to_tsquery, joined with OR; English stop words are dropped
    # by the text-search configuration itself.
    words = sorted({w.lower() for w in _WORD.findall(query)} - _GENERIC)
    return " | ".join(words[:24])


_SQL = """
WITH params AS (
  SELECT CAST(:embedding AS vector) AS e,
         CASE WHEN :tsq = '' THEN NULL ELSE to_tsquery('english', :tsq) END AS ts
),
candidates AS (
  SELECT c.id, c.embedding, c.search
  FROM app.document_chunks c
  JOIN app.policy_documents d ON d.id = c.document_id
  JOIN app.products p ON p.id = d.product_id
  WHERE d.is_current AND d.locale = :locale
    AND (CAST(:product_ids AS uuid[]) IS NULL OR d.product_id = ANY(CAST(:product_ids AS uuid[])))
    AND (CAST(:line AS text) IS NULL OR p.line = CAST(:line AS text))
),
vec AS (
  SELECT c.id, row_number() OVER (ORDER BY c.embedding <=> params.e) AS r
  FROM candidates c, params
  ORDER BY c.embedding <=> params.e
  LIMIT 20
),
fts AS (
  SELECT c.id, row_number() OVER (ORDER BY ts_rank_cd(c.search, params.ts) DESC) AS r
  FROM candidates c, params
  WHERE params.ts IS NOT NULL AND c.search @@ params.ts
  ORDER BY ts_rank_cd(c.search, params.ts) DESC
  LIMIT 20
),
fused AS (
  SELECT id, sum(1.0 / (:k + r)) AS score
  FROM (SELECT id, r FROM vec UNION ALL SELECT id, r FROM fts) ranked
  GROUP BY id
)
SELECT c.id AS chunk_id, d.id AS document_id, d.product_id, p.code AS product_code,
       d.title AS document_title, c.section_ref, c.page_start, c.page_end, c.content,
       f.score
FROM fused f
JOIN app.document_chunks c ON c.id = f.id
JOIN app.policy_documents d ON d.id = c.document_id
JOIN app.products p ON p.id = d.product_id
ORDER BY f.score DESC, c.id
LIMIT :limit
"""


async def retrieve(
    session: AsyncSession,
    embedder: Embedder,
    query: str,
    *,
    locale: str = "en",
    product_id: uuid.UUID | None = None,
    product_ids: Sequence[uuid.UUID] | None = None,
    line: str | None = None,
    limit: int = 6,
) -> list[RetrievedChunk]:
    """Top chunks for `query`, optionally limited to one product or a set of products."""
    scope = [*(product_ids or ()), *([product_id] if product_id else [])]
    embedding = await embedder.embed_query(query)
    rows = await session.execute(
        text(_SQL),
        {
            "embedding": "[" + ",".join(f"{x:.6f}" for x in embedding) + "]",
            "tsq": _or_tsquery(query),
            "locale": locale,
            "product_ids": [str(p) for p in scope] or None,
            "line": line,
            "k": RRF_K,
            "limit": max(1, min(limit, 8)),
        },
    )
    return [
        RetrievedChunk(
            chunk_id=row.chunk_id,
            document_id=row.document_id,
            product_id=row.product_id,
            product_code=row.product_code,
            document_title=row.document_title,
            section_ref=row.section_ref,
            page_start=row.page_start,
            page_end=row.page_end,
            content=row.content,
            score=float(row.score),
        )
        for row in rows
    ]
