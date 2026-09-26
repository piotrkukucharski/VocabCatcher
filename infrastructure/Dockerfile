# ==========================================
# STAGE 1: Frontend Build (Node / Vite)
# ==========================================
FROM node:20-alpine AS frontend-builder
WORKDIR /build

COPY web/package*.json ./
RUN npm ci

COPY web/ ./
RUN npm run build

# ==========================================
# STAGE 2: Python Backend & Pandoc Environment
# ==========================================
FROM python:3.11-slim AS runner

# Install OS dependencies: Pandoc is strictly required for pypandoc file conversion
RUN apt-get update && apt-get install -y --no-install-recommends \
    pandoc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv for deterministic dependency installation
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Create a dedicated non-root application user for production security
RUN groupadd -r appgroup && useradd -r -g appgroup -d /app -s /sbin/nologin appuser

WORKDIR /app

# Install dependencies using uv and uv.lock for frozen, deterministic production builds
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy application source and compiled frontend bundle
COPY app/ ./app/
COPY --from=frontend-builder /build/dist ./web/dist

# Set ownership to non-root user
RUN chown -R appuser:appgroup /app

USER appuser

ENV HOST=0.0.0.0 \
    PORT=8000 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Production server command with multiple workers
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4", "--no-access-log"]

