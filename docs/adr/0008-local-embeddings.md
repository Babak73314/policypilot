# ADR-0008: Local embeddings with fastembed

- Status: Accepted
- Date: 2026-11-20

## Context

RAG over policy documents needs an embedding model. Anthropic does not offer one.
The options are hosted APIs (Voyage, OpenAI, Bedrock Titan) or a local model.
The corpus is small (tens of documents), and the quick start has to work with no API keys.

## Decision

- Embed with **fastembed** and `BAAI/bge-small-en-v1.5`: 384 dimensions, ONNX on CPU, no key. The
  model files are downloaded once at image build time and baked into the API image, so the
  container never needs network access to embed.
- Every chunk stores `embedding_model`. If the configured model differs from the stored one, the
  ingestion command refuses to mix them and asks for a re-index.
- **Plumbing tests** use a deterministic hashing embedder with the same dimension, so unit and
  integration tests need no model. **Retrieval-quality tests**, including paraphrases with no
  keyword overlap, run against the real model inside the API image in the CI compose job, with
  Docker layer caching (review 01, finding 30).
- Retrieval is hybrid (ADR-0004): full-text search covers exact terms such as clause names, and
  vectors cover paraphrases.

## Consequences

- Retrieval costs nothing and works offline. The API image grows by about 150 MB.
- English-only quality is fine for Phase 1. A second language may need a multilingual model, which
  means a migration to a new column dimension and a re-index. The `Embedder` interface makes that a
  configuration change plus `make reindex`.
- The same model runs on AWS, so no Bedrock access is needed.

## Alternatives considered

- **Bedrock Titan v2 (1,024 dimensions):** available on AWS by IAM role, but it ties
  local development to AWS credentials.
- **Voyage or OpenAI embeddings:** an extra key and per-call cost, with no benefit at this corpus size.
