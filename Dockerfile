FROM python:3.12-slim

ARG MEMPALACE_VERSION=develop

# Build deps for native packages (chromadb wheels may need them on arm64)
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install MemPalace + mcp-proxy + auth proxy deps
RUN pip install --no-cache-dir \
    "mempalace @ git+https://github.com/MemPalace/mempalace.git@${MEMPALACE_VERSION}" \
    mcp-proxy \
    "mcp<2" \
    starlette \
    httpx \
    uvicorn \
    python-multipart

# Auth proxy and entrypoint script
COPY auth_proxy.py /app/auth_proxy.py
COPY start.sh /start.sh
RUN chmod +x /start.sh

WORKDIR /app

# MCP_AUTH_TOKEN: set this to enable Bearer token auth.
# If unset, the proxy forwards all requests without auth check.
ENV MCP_AUTH_TOKEN=""
# PUBLIC_URL: public HTTPS base URL, used in the OAuth metadata for claude.ai
ENV PUBLIC_URL=""

# All MemPalace data lives under /root/.mempalace:
#   palace/                  — drawers + ChromaDB vectors
#   knowledge_graph.sqlite3  — knowledge graph
#   config.json              — configuration
#   wal/                     — write-ahead log
VOLUME ["/root/.mempalace"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import socket; socket.create_connection(('127.0.0.1', 8081), 3)" || exit 1

EXPOSE 8080

# start.sh: mcp-proxy on :8081 (internal) + auth_proxy on :8080 (external)
ENTRYPOINT ["/start.sh"]
