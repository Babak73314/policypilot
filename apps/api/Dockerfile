# PolicyPilot API image. Build context: the repository root (it needs data/seed).
#   docker build -f apps/api/Dockerfile -t policypilot-api .
FROM python:3.12-slim AS app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    FASTEMBED_CACHE_DIR=/opt/fastembed \
    ORT_DISABLE_TELEMETRY=1 \
    SEED_DIR=/app/data/seed \
    DOCUMENTS_OUTPUT_DIR=/tmp/policypilot-documents \
    PATH="/app/.venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /bin/uv
WORKDIR /app

# Dependencies first, for layer caching.
COPY apps/api/pyproject.toml apps/api/uv.lock apps/api/README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project

COPY apps/api/src ./src
COPY apps/api/migrations ./migrations
COPY apps/api/alembic.ini ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

# Bake the embedding model into the image so containers never download it (ADR-0008).
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/opt/fastembed')"

COPY data/seed ./data/seed

# The code and the virtualenv stay root-owned and read-only for the service user; only the model
# cache (fastembed may write lock files) and the rendered-PDF directory are writable.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin policypilot \
    && mkdir -p /tmp/policypilot-documents \
    && chown -R policypilot /opt/fastembed /tmp/policypilot-documents
USER policypilot

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"
CMD ["uvicorn", "--factory", "policypilot.main:app_factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*", "--no-access-log"]
