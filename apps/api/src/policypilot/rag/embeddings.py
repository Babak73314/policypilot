"""Embedders (ADR-0008): fastembed locally and on AWS, a deterministic hashing embedder in tests."""

from __future__ import annotations

import asyncio
import hashlib
import math
import os
import re
from typing import Protocol

from policypilot.config import Settings


class Embedder(Protocol):
    @property
    def model_name(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


_TOKEN = re.compile(r"[a-z0-9]+")


class HashingEmbedder:
    """Deterministic bag-of-words hashing into a fixed-size, L2-normalised vector.

    It has no semantic understanding; it exists so plumbing tests run without a model download.
    """

    def __init__(self, dimensions: int = 384) -> None:
        self.dimensions = dimensions
        self.model_name = f"hashing-{dimensions}"

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in _TOKEN.findall(text.lower()):
            digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[bucket] += sign
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class FastEmbedEmbedder:
    """`BAAI/bge-small-en-v1.5` (384 dims) on CPU via ONNX Runtime; no API key."""

    def __init__(self, model_name: str, dimensions: int, cache_dir: str | None = None) -> None:
        # onnxruntime 1.32 ships a telemetry client on Linux; opt out before it loads.
        os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")
        from fastembed import TextEmbedding

        self.model_name = model_name
        self.dimensions = dimensions
        self._model = TextEmbedding(model_name=model_name, cache_dir=cache_dir)

    def _embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(x) for x in vector] for vector in self._model.embed(texts)]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await asyncio.to_thread(self._embed, texts)

    async def embed_query(self, text: str) -> list[float]:
        # bge models recommend a query instruction prefix for retrieval.
        prefixed = f"Represent this sentence for searching relevant passages: {text}"
        return (await asyncio.to_thread(self._embed, [prefixed]))[0]


def build_embedder(settings: Settings) -> Embedder:
    if settings.embedding_provider == "hashing":
        return HashingEmbedder(settings.embedding_dimensions)
    return FastEmbedEmbedder(
        settings.embedding_model, settings.embedding_dimensions, settings.fastembed_cache_dir
    )
