"""
Lightweight auth proxy for MemPalace MCP server.

Checks the Authorization: Bearer <token> header against MCP_AUTH_TOKEN.
Forwards matching requests to mcp-proxy on localhost:8081.
If MCP_AUTH_TOKEN is unset, auth is skipped (useful for local dev).
"""

import os
import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route, Mount

UPSTREAM = "http://localhost:8081"
AUTH_TOKEN = os.environ.get("MCP_AUTH_TOKEN", "")

# Headers that must not be forwarded to the upstream
_HOP_BY_HOP = {
    "host", "connection", "keep-alive", "transfer-encoding",
    "te", "trailer", "proxy-authorization", "proxy-authenticate",
    "upgrade",
}


async def proxy(request: Request) -> Response:
    # --- Auth check ---
    if AUTH_TOKEN:
        auth_header = request.headers.get("authorization", "")
        if auth_header != f"Bearer {AUTH_TOKEN}":
            return Response("Unauthorized", status_code=401,
                            media_type="text/plain")

    # --- Build upstream URL ---
    path = request.url.path or "/"
    upstream_url = UPSTREAM + path
    if request.url.query:
        upstream_url += "?" + request.url.query

    # --- Strip hop-by-hop headers ---
    forward_headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in _HOP_BY_HOP
    }

    # --- Stream to upstream (required for SSE) ---
    client = httpx.AsyncClient(timeout=None)
    upstream_request = client.build_request(
        method=request.method,
        url=upstream_url,
        headers=forward_headers,
        content=await request.body(),
    )
    upstream_response = await client.send(upstream_request, stream=True)

    response_headers = {
        k: v for k, v in upstream_response.headers.items()
        if k.lower() not in _HOP_BY_HOP
    }

    async def stream_and_close():
        try:
            async for chunk in upstream_response.aiter_bytes():
                yield chunk
        finally:
            await upstream_response.aclose()
            await client.aclose()

    return StreamingResponse(
        stream_and_close(),
        status_code=upstream_response.status_code,
        headers=response_headers,
        media_type=upstream_response.headers.get("content-type"),
    )


app = Starlette(routes=[
    Mount("/", app=Starlette(routes=[
        Route("/{path:path}", proxy,
              methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]),
        Route("/", proxy,
              methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]),
    ])),
])

if __name__ == "__main__":
    if not AUTH_TOKEN:
        print("WARNING: MCP_AUTH_TOKEN is not set — auth is disabled", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=8080)
