# Engram — Knowledge Base MCP Server
# Persistent knowledge base with hybrid SQLite FTS5 + semantic search

FROM python:3.13-alpine AS base

WORKDIR /app

# Install Python dependencies
COPY src/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY src/ ./

# Pre-download the embedding model into the image so runtime and tests
# never need network access for it (cache dir chowned to engram below).
ENV HF_HOME=/app/.cache/huggingface
RUN python -c "from model2vec import StaticModel; StaticModel.from_pretrained('minishlab/potion-multilingual-128M')"

# Non-root user
RUN addgroup -S engram && adduser -S engram -G engram
RUN mkdir -p /knowledge && chown engram:engram /knowledge
RUN chown -R engram:engram /app/.cache
USER engram

# Default configuration (override via docker run -e or --arg)
ENV ENGRAM_DATA_PATH=/knowledge
ENV ENGRAM_TRANSPORT=stdio
ENV ENGRAM_HOST=0.0.0.0
ENV ENGRAM_PORT=8192
ENV ENGRAM_EMBEDDING_MODEL=minishlab/potion-multilingual-128M

# Data volume
VOLUME /knowledge

EXPOSE 8192

ENTRYPOINT ["python", "server.py"]
CMD []

# ---------------------------------------------------------------------------
# Test stage — separate image, not part of the production build.
# Build:  docker build --target test -t engram-test .
# Run:    docker run --rm engram-test
# ---------------------------------------------------------------------------

FROM base AS test

USER root

RUN pip install --no-cache-dir pytest==8.4.2

# tests/ expects a sibling src/ dir (Path(__file__).parent.parent / "src");
# the base image flattens src/ straight into /app, so point a symlink at it
# instead of restructuring the production image layout.
RUN ln -s /app /app/src
COPY tests/ ./tests/
RUN chown engram:engram /app /app/tests

USER engram

ENTRYPOINT ["python", "-m", "pytest"]
CMD ["tests/", "-v"]
