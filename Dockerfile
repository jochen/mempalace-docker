FROM python:3.12-slim

ARG MEMPALACE_VERSION=develop

# Build deps for native packages (chromadb wheels may need them on arm64)
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install MemPalace (>= 3.10.0 for --transport http) + auth proxy deps
RUN pip install --no-cache-dir \
    "mempalace @ git+https://github.com/MemPalace/mempalace.git@${MEMPALACE_VERSION}" \
    starlette \
    httpx \
    uvicorn \
    python-multipart

# Auth proxy, backend readiness probe and entrypoint script
COPY auth_proxy.py /app/auth_proxy.py
COPY backend_ready.py /app/backend_ready.py
COPY start.sh /start.sh
RUN chmod +x /start.sh

WORKDIR /app

# MCP_AUTH_TOKEN: set this to enable Bearer token auth.
# If unset, the proxy forwards all requests without auth check.
ENV MCP_AUTH_TOKEN=""
# PUBLIC_URL: public HTTPS base URL, used in the OAuth metadata for claude.ai
ENV PUBLIC_URL=""
# No peer mesh: keep MemPalace's logstream sync thread off
ENV MEMPALACE_SYNC_INTERVAL=0
# Load the embedding model at startup, not on the first claude.ai call
ENV MEMPALACE_EAGER_WARMUP=1

# All MemPalace data lives under /root/.mempalace:
#   palace/                  — drawers + ChromaDB vectors
#   knowledge_graph.sqlite3  — knowledge graph
#   config.json              — configuration
#   wal/                     — write-ahead log
VOLUME ["/root/.mempalace"]

# Backend /healthz (liveness) + auth proxy answering on :8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8081/healthz', timeout=3); u.urlopen('http://127.0.0.1:8080/.well-known/oauth-authorization-server', timeout=3)" || exit 1

EXPOSE 8080

# start.sh: MemPalace native HTTP on 127.0.0.1:8081 (internal)
#           + auth_proxy on :8080 (external); exits if either dies
ENTRYPOINT ["/start.sh"]
