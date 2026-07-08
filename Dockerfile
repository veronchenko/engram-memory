# Engram — Knowledge Base MCP Server
# Persistent knowledge base with hybrid SQLite FTS5 + semantic search
#
# Production image only — this is the sole stage, so a plain
# `docker build -t <tag> .` (no --target) always builds production.
# The test image lives in tests/Dockerfile and builds FROM this image.

FROM python:3.13-alpine

WORKDIR /app

# Install Python dependencies
COPY src/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download the embedding model into the image so runtime and tests
# never need network access for it (cache dir chowned to engram below).
# Done before COPY src/ so editing app code doesn't invalidate this layer
# and force a re-download.
ENV HF_HOME=/app/.cache/huggingface
RUN python -c "from model2vec import StaticModel; StaticModel.from_pretrained('minishlab/potion-multilingual-128M')"

# Copy application
COPY src/ ./
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Non-root user
RUN addgroup -S engram && adduser -S engram -G engram
RUN mkdir -p /knowledge && chown engram:engram /knowledge
RUN chown -R engram:engram /app/.cache
USER engram

# Default configuration (override via docker run -e or --arg)
ENV ENGRAM_DATA_PATH=/knowledge
ENV ENGRAM_TRANSPORT=stdio
ENV ENGRAM_HOST=0.0.0.0
# ENGRAM_PORT/ENGRAM_DASHBOARD_PORT are starting ports: the first free port
# at or above them is used, so the actual bound port can differ.
ENV ENGRAM_PORT=8192
ENV ENGRAM_DASHBOARD_HOST=0.0.0.0
ENV ENGRAM_DASHBOARD_PORT=8193
ENV ENGRAM_EMBEDDING_MODEL=minishlab/potion-multilingual-128M
# Dashboard (API + frontend) is off by default — only the MCP backend
# starts. Set to 1/true/yes to also run the dashboard as a second process.
ENV ENGRAM_ENABLE_DASHBOARD=1

# Data volume
VOLUME /knowledge

EXPOSE 8192 8193

ENTRYPOINT ["docker-entrypoint.sh"]
CMD []
