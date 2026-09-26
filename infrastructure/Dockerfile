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

# Install uv for Python package management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install Python requirements via uv
COPY pyproject.toml README.md ./
RUN uv pip install --system --no-cache -r pyproject.toml

# Copy backend application
COPY app/ ./app/

# Copy built frontend assets from Stage 1 into the location expected by FastAPI
COPY --from=frontend-builder /build/dist ./web/dist

ENV HOST=0.0.0.0
ENV PORT=8000
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
