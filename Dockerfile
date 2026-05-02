FROM python:3.12-slim

# Build deps for native packages (chromadb wheels may need them on arm64)
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install MemPalace from develop branch + mcp-proxy + auth proxy deps
RUN pip install --no-cache-dir \
    "mempalace @ git+https://github.com/MemPalace/mempalace.git@develop" \
    mcp-proxy \
    starlette \
    httpx \
    uvicorn

# Auth proxy and entrypoint script
COPY auth_proxy.py /app/auth_proxy.py
COPY start.sh /start.sh
RUN chmod +x /start.sh

WORKDIR /app

# Palace data and ChromaDB both live under /data (single volume)
ENV MEMPALACE_PALACE_PATH=/data

# MCP_AUTH_TOKEN: set this to enable Bearer token auth.
# If unset, the proxy forwards all requests without auth check.
ENV MCP_AUTH_TOKEN=""

VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8081/')" || exit 1

EXPOSE 8080

# start.sh: mcp-proxy on :8081 (internal) + auth_proxy on :8080 (external)
ENTRYPOINT ["/start.sh"]
