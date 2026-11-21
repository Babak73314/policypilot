# ADR-0004: One PostgreSQL database with pgvector for data, vectors and checkpoints

- Status: Accepted
- Date: 2026-11-20

## Context

PolicyPilot needs three kinds of storage:

- relational data: carriers, products, rate tables, customers, conversations, leads, quotes,
  applications, the audit log and the outbox;
- vectors for policy-document retrieval;
- durable LangGraph checkpoints, which interrupts and resumes require.

The planned AWS deployment can start on an existing Aurora PostgreSQL 16 cluster with pgvector
(ADR-0015).

## Decision

- Use **one PostgreSQL database, `policypilot`**, split into schemas:
  - `app` holds the domain tables and is managed by **Alembic** migrations.
  - `langgraph` holds the checkpoint tables, created by `AsyncPostgresSaver.setup()`.
    `langgraph-checkpoint-postgres` has no schema option, so the checkpointer's connection sets
    `search_path=langgraph`.
- Use **pgvector** with an HNSW index (`vector_cosine_ops`) on `app.document_chunks.embedding`, plus
  a generated `tsvector` column for full-text search. Retrieval is **hybrid**: vector similarity and
  full-text ranking, combined with reciprocal rank fusion.
- Develop locally on **PostgreSQL 16** (`pgvector/pgvector:0.8.7-pg16-trixie`) to match the shared
  Aurora 16 cluster. The code uses only pgvector features available since 0.5 (HNSW and cosine
  distance), so the version Aurora ships is enough.
- n8n gets its **own database, `n8n`**, with its own role. It never shares the `policypilot` database.
- Migrations refuse to run unless `current_database()` is `policypilot`. This guards against running
  them against the wrong database on a shared cluster.
- Money is stored as integer minor units plus an ISO 4217 currency code.

## Consequences

- There is one backup, one connection string and one thing to run locally.
- On a shared cluster, any open connection keeps Aurora Serverless v2 from auto-pausing. The pools
  are therefore sized small and release idle connections (`docs/deployment/aws.md`).
- Moving to a separate instance later is a `pg_dump`/`pg_restore` of one database.

## Alternatives considered

- **A separate vector database (Qdrant, OpenSearch):** another service to run, and no benefit at a
  few thousand chunks.
- **SQLite checkpoints:** not durable across containers, and not what production would use.
- **DynamoDB:** no SQL, no vectors, and it does not match the existing infrastructure.
