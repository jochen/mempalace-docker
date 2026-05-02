# mempalace-docker

Docker image that runs [MemPalace](https://github.com/MemPalace/mempalace) as an MCP server exposed over SSE/HTTP via [mcp-proxy](https://github.com/sparfenyuk/mcp-proxy).

**Image:** `ghcr.io/jochen/mempalace-docker:latest`
**Platforms:** `linux/amd64`, `linux/arm64` (Raspberry Pi)

---

## Quick start

```bash
docker run -d \
  --name mempalace \
  -p 8080:8080 \
  -v mempalace-data:/data \
  ghcr.io/jochen/mempalace-docker:latest
```

The MCP endpoint is available at `http://localhost:8080/sse`.

---

## docker-compose

```yaml
services:
  mempalace:
    image: ghcr.io/jochen/mempalace-docker:latest
    restart: unless-stopped
    ports:
      - "8080:8080"
    volumes:
      - mempalace-data:/data

volumes:
  mempalace-data:
```

---

## Configuration

| Environment variable | Default | Description |
|---|---|---|
| `MEMPALACE_PALACE_PATH` | `/data` | Directory for palace files **and** ChromaDB embeddings |

Both the palace structure and the ChromaDB vector store are written to the same directory, so a single volume at `/data` persists everything.

---

## MCP client setup

### claude.ai Web (Custom Connector)

In the Claude Web UI add a custom MCP connector with the URL:

```
http://<your-host>:8080/sse
```

### Claude CLI / claude-code

Add to your `~/.claude.json` or project config:

```json
{
  "mcpServers": {
    "mempalace": {
      "type": "sse",
      "url": "http://<your-host>:8080/sse"
    }
  }
}
```

---

## Architecture

```
MCP client (claude.ai / claude-cli)
        │  SSE / Streamable HTTP
        ▼
  mcp-proxy :8080
        │  stdio JSON-RPC
        ▼
  python -m mempalace.mcp_server
        │
        ▼
  /data  (palace files + ChromaDB)
```

MemPalace's MCP server speaks stdio only. `mcp-proxy` wraps it and exposes the protocol over HTTP/SSE without any code changes to MemPalace itself.

---

## Auth

The container has no built-in authentication. Put a reverse proxy (nginx, Caddy, Traefik) in front and handle auth there — Basic Auth or bearer token at the proxy level is the recommended approach.

---

## Building locally

```bash
git clone https://github.com/jochen/mempalace-docker.git
cd mempalace-docker
docker build -t mempalace-local .
```

Multi-arch (requires `docker buildx`):

```bash
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t mempalace-local \
  --load .
```

---

## Updates

The image always installs MemPalace from the `develop` branch at build time. To get the latest MemPalace version, trigger a new build or pull the freshly built image:

```bash
docker pull ghcr.io/jochen/mempalace-docker:latest
```
