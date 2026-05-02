FROM python:3.12-slim

# Build deps for native packages (chromadb wheels may need them on arm64)
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install MemPalace from develop branch + mcp-proxy as SSE wrapper
RUN pip install --no-cache-dir \
    "mempalace @ git+https://github.com/MemPalace/mempalace.git@develop" \
    mcp-proxy

# Palace data and ChromaDB both live under /data (single volume)
ENV MEMPALACE_PALACE_PATH=/data

VOLUME ["/data"]

EXPOSE 8080

# mcp-proxy wraps the stdio MCP server and exposes it as SSE/HTTP on port 8080
ENTRYPOINT ["mcp-proxy", "--port", "8080", "--", "python", "-m", "mempalace.mcp_server"]
