# mempalace-docker

Docker image that runs [MemPalace](https://github.com/MemPalace/mempalace) as an MCP server exposed over Streamable HTTP via [mcp-proxy](https://github.com/sparfenyuk/mcp-proxy).

**Image:** `ghcr.io/jochen/mempalace-docker:latest`
**Platforms:** `linux/amd64`, `linux/arm64` (Raspberry Pi)

---

## Quick start

```bash
docker run -d \
  --name mempalace \
  -p 8080:8080 \
  -v mempalace-data:/root/.mempalace \
  ghcr.io/jochen/mempalace-docker:latest
```

The MCP endpoint is available at `http://localhost:8080/mcp` (Streamable HTTP).

---

## docker-compose

```yaml
services:
  mempalace:
    image: ghcr.io/jochen/mempalace-docker:latest
    restart: unless-stopped
    ports:
      - "8080:8080"
    environment:
      MCP_AUTH_TOKEN: "${MCP_AUTH_TOKEN}"   # set in .env or shell
    volumes:
      - mempalace-data:/root/.mempalace

volumes:
  mempalace-data:
```

---

## Configuration

| Environment variable | Default | Description |
|---|---|---|
| `MCP_AUTH_TOKEN` | _(unset)_ | Bearer token for auth. If unset, auth is disabled — safe for local use, **set this for any network-exposed deployment** |

All MemPalace data lives under `/root/.mempalace` — mount this as a single volume to persist everything:

| Path | Contents |
|---|---|
| `/root/.mempalace/palace/` | Drawers + ChromaDB vectors |
| `/root/.mempalace/knowledge_graph.sqlite3` | Knowledge graph |
| `/root/.mempalace/config.json` | Configuration |
| `/root/.mempalace/wal/` | Write-ahead log |

---

## MCP client setup

### claude.ai Web (Custom Connector)

In the Claude Web UI add a custom MCP connector:

- **URL:** `http://<your-host>:8080/mcp`
- **Auth:** Bearer token → enter the value of `MCP_AUTH_TOKEN`

### Claude CLI / claude-code

Add to your `~/.claude.json` or project config:

```json
{
  "mcpServers": {
    "mempalace": {
      "type": "http",
      "url": "http://<your-host>:8080/mcp",
      "headers": {
        "Authorization": "Bearer <your-token>"
      }
    }
  }
}
```

---

## Architecture

```
MCP client (claude.ai / claude-cli)
        │  Streamable HTTP  +  Authorization: Bearer <token>
        ▼
  auth_proxy.py :8080   ← checks MCP_AUTH_TOKEN, returns 401 on mismatch
        │  forwards matching requests
        ▼
  mcp-proxy :8081       ← stdio → Streamable HTTP bridge (internal only)
        │  stdio JSON-RPC
        ▼
  python -m mempalace.mcp_server
        │
        ▼
  /root/.mempalace  (palace, knowledge graph, config, wal)
```

MemPalace's MCP server speaks stdio only. `mcp-proxy` wraps it as Streamable HTTP on the internal port 8081. `auth_proxy.py` (Starlette + httpx) sits in front on port 8080, validates the Bearer token, and streams through.

## Auth

Bearer token auth is built into the container via `auth_proxy.py`:

- Set `MCP_AUTH_TOKEN` to enable it — any request without `Authorization: Bearer <token>` gets a `401`
- Leave `MCP_AUTH_TOKEN` unset to disable auth (prints a warning on startup) — useful for local dev behind a firewall

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

## License

MIT — see [LICENSE](LICENSE).

---

## Updates

The image always installs MemPalace from the `develop` branch at build time. To get the latest MemPalace version, trigger a new build or pull the freshly built image:

```bash
docker pull ghcr.io/jochen/mempalace-docker:latest
```
