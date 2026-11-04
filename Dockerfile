# Multi-stage build for efficient container size
FROM python:3.12-slim AS builder

ARG VERSION="unknown"
ARG COMMIT_SHA="unknown"
ARG BUILD_DATE="unknown"

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

# Install dependencies first (layer-cached when only src changes)
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev --no-install-project

# Install the project itself
COPY . .
RUN uv sync --frozen --no-dev

# Production stage -- slim image, no uv, no build tools
FROM python:3.12-slim AS production

# Re-declared here (ARGs do not cross FROM boundaries) so the LABELs below
# can reference them without a docker build warning.
ARG VERSION="unknown"
ARG COMMIT_SHA="unknown"
ARG BUILD_DATE="unknown"

RUN groupadd -g 1001 huggingface && \
    useradd -u 1001 -g huggingface -s /bin/sh -m huggingface

# python:3.12-slim ships neither curl nor wget; the HEALTHCHECK below needs one.
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder --chown=huggingface:huggingface /app/.venv /app/.venv
COPY --from=builder --chown=huggingface:huggingface /app/src /app/src

ENV PATH="/app/.venv/bin:$PATH"

# Default environment -- HTTP transport, credentials always per-request (no
# env-mode auth exists in this service, see .env.example)
ENV MCP_HTTP_PORT=8080
ENV MCP_HTTP_HOST=0.0.0.0

USER huggingface

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD curl -fsS http://localhost:8080/health || exit 1

CMD ["python", "-m", "huggingface_mcp"]

LABEL org.opencontainers.image.title="huggingface-mcp"
LABEL org.opencontainers.image.description="Hugging Face Hub + Inference Providers MCP server for MSPbots Agent platform"
LABEL org.opencontainers.image.version="${VERSION}"
LABEL org.opencontainers.image.created="${BUILD_DATE}"
LABEL org.opencontainers.image.revision="${COMMIT_SHA}"
LABEL org.opencontainers.image.licenses="Apache-2.0"
