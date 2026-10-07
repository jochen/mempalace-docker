"""
Lightweight auth proxy for MemPalace MCP server.

Accepts credentials via:
  - Authorization: Bearer <MCP_AUTH_TOKEN>   (claude-cli, API clients)
  - Authorization: Bearer <oauth token>      (claude.ai Web Custom Connector)
  - ?token=<MCP_AUTH_TOKEN> query parameter  (legacy, scripts)

claude.ai custom connectors only speak OAuth 2.1 (DCR + PKCE), so this proxy
also acts as a minimal single-user OAuth authorization server. The login page
asks for MCP_AUTH_TOKEN; clients and refresh tokens are persisted to
OAUTH_STATE_FILE so connectors survive container restarts.

Forwards authorized requests to mcp-proxy on localhost:8081. If MCP_AUTH_TOKEN
is unset, auth is skipped.

Extra endpoints:
  POST /mine  — upload conversation files, run mempalace mine, return JSON
  OAuth: /.well-known/oauth-protected-resource[/...],
         /.well-known/oauth-authorization-server, /register, /authorize, /token
"""

import asyncio
import base64
import hashlib
import html
import json
import os
import re
import secrets
import shutil
import time
import uuid
from urllib.parse import urlencode, urlsplit
import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import (
    HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse,
)
from starlette.routing import Route, Mount

UPSTREAM = "http://localhost:8081"
AUTH_TOKEN = os.environ.get("MCP_AUTH_TOKEN", "")
# Public base URL as seen by clients, e.g. https://memory.example.com.
# If unset, it is derived from the request (honours X-Forwarded-* headers).
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")
OAUTH_STATE_FILE = os.environ.get(
    "OAUTH_STATE_FILE", "/root/.mempalace/oauth_state.json")

ACCESS_TOKEN_TTL = 3600
REFRESH_TOKEN_TTL = 90 * 24 * 3600
AUTH_CODE_TTL = 600

# Headers that must not be forwarded to the upstream
_HOP_BY_HOP = {
    "host", "connection", "keep-alive", "transfer-encoding",
    "te", "trailer", "proxy-authorization", "proxy-authenticate",
    "upgrade",
}

# Serialize mine runs — ChromaDB does not support concurrent writers
_mine_lock = asyncio.Lock()

_SAFE_NAME = re.compile(r"[^a-zA-Z0-9_\-.]")


# ---------------------------------------------------------------------------
# OAuth state (single user, persisted as JSON)
# ---------------------------------------------------------------------------

def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _load_state() -> dict:
    try:
        with open(OAUTH_STATE_FILE) as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        state = {}
    for key in ("clients", "access_tokens", "refresh_tokens"):
        state.setdefault(key, {})
    return state


_state = _load_state()
_auth_codes: dict = {}  # in memory only, short-lived


def _save_state() -> None:
    now = time.time()
    for key in ("access_tokens", "refresh_tokens"):
        _state[key] = {
            k: v for k, v in _state[key].items() if v["expires_at"] > now
        }
    os.makedirs(os.path.dirname(OAUTH_STATE_FILE) or ".", exist_ok=True)
    tmp = OAUTH_STATE_FILE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(_state, fh)
    os.replace(tmp, OAUTH_STATE_FILE)


def _base_url(request: Request) -> str:
    if PUBLIC_URL:
        return PUBLIC_URL
    return str(request.base_url).rstrip("/")


def _check_auth(request: Request) -> bool:
    if not AUTH_TOKEN:
        return True
    auth_header = request.headers.get("authorization", "")
    query_token = request.query_params.get("token", "")
    if secrets.compare_digest(auth_header, f"Bearer {AUTH_TOKEN}"):
        return True
    if query_token and secrets.compare_digest(query_token, AUTH_TOKEN):
        return True
    if auth_header.startswith("Bearer "):
        entry = _state["access_tokens"].get(_hash(auth_header[7:]))
        if entry and entry["expires_at"] > time.time():
            return True
    return False


def _unauthorized(request: Request) -> Response:
    # The resource_metadata pointer is what makes claude.ai start OAuth
    metadata_url = (
        _base_url(request) + "/.well-known/oauth-protected-resource"
        + request.url.path.rstrip("/")
    )
    return Response(
        "Unauthorized", status_code=401, media_type="text/plain",
        headers={"WWW-Authenticate": f'Bearer resource_metadata="{metadata_url}"'},
    )


def _oauth_error(error: str, description: str = "", status: int = 400) -> Response:
    body = {"error": error}
    if description:
        body["error_description"] = description
    return JSONResponse(body, status_code=status,
                        headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------------------
# OAuth endpoints
# ---------------------------------------------------------------------------

async def protected_resource_metadata(request: Request) -> Response:
    base = _base_url(request)
    path = request.path_params.get("path", "")
    resource = base + ("/" + path if path else "/mcp")
    return JSONResponse({
        "resource": resource,
        "authorization_servers": [base],
        "scopes_supported": ["mcp"],
        "bearer_methods_supported": ["header"],
    })


async def authorization_server_metadata(request: Request) -> Response:
    base = _base_url(request)
    return JSONResponse({
        "issuer": base,
        "authorization_endpoint": base + "/authorize",
        "token_endpoint": base + "/token",
        "registration_endpoint": base + "/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": [
            "none", "client_secret_post", "client_secret_basic",
        ],
        "scopes_supported": ["mcp"],
    })


async def register_endpoint(request: Request) -> Response:
    try:
        meta = await request.json()
    except ValueError:
        return _oauth_error("invalid_client_metadata", "body must be JSON")
    redirect_uris = meta.get("redirect_uris")
    if (not isinstance(redirect_uris, list) or not redirect_uris
            or not all(isinstance(u, str) for u in redirect_uris)):
        return _oauth_error("invalid_redirect_uri", "redirect_uris required")
    for uri in redirect_uris:
        parts = urlsplit(uri)
        loopback = parts.hostname in ("localhost", "127.0.0.1")
        if parts.scheme != "https" and not (parts.scheme == "http" and loopback):
            return _oauth_error("invalid_redirect_uri", f"not allowed: {uri}")

    client_id = secrets.token_urlsafe(16)
    client = {
        "client_id": client_id,
        "client_id_issued_at": int(time.time()),
        "client_name": str(meta.get("client_name", ""))[:200],
        "redirect_uris": redirect_uris,
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        # Public client: PKCE protects the code exchange, secrets are ignored
        "token_endpoint_auth_method": "none",
    }
    _state["clients"][client_id] = client
    _save_state()
    return JSONResponse(client, status_code=201)


def _redirect_uri_ok(client: dict, redirect_uri: str) -> bool:
    if redirect_uri in client["redirect_uris"]:
        return True
    # RFC 8252: loopback redirects match regardless of port
    target = urlsplit(redirect_uri)
    if target.hostname not in ("localhost", "127.0.0.1"):
        return False
    for uri in client["redirect_uris"]:
        reg = urlsplit(uri)
        if (reg.scheme, reg.hostname, reg.path) == (
                target.scheme, target.hostname, target.path):
            return True
    return False


_LOGIN_PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MemPalace – Anmelden</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:24rem;margin:4rem auto;padding:0 1rem}}
input,button{{font-size:1rem;padding:.5rem;width:100%;box-sizing:border-box;margin:.25rem 0}}
.err{{color:#b00}}
</style></head><body>
<h1>MemPalace</h1>
<p><b>{client}</b> möchte auf deine MemPalace zugreifen.<br>
Weiterleitung an <code>{host}</code>.</p>
{error}
<form method="post" action="/authorize">
{hidden}
<input type="password" name="password" placeholder="MCP_AUTH_TOKEN" autofocus required>
<button type="submit">Zugriff erlauben</button>
</form></body></html>"""

_AUTHORIZE_PARAMS = (
    "response_type", "client_id", "redirect_uri", "state",
    "code_challenge", "code_challenge_method", "scope", "resource",
)


async def authorize_endpoint(request: Request) -> Response:
    if request.method == "POST":
        params = dict(await request.form())
    else:
        params = dict(request.query_params)

    client = _state["clients"].get(params.get("client_id", ""))
    redirect_uri = params.get("redirect_uri", "")
    # Never redirect to an unverified URI — show the error instead
    if not client:
        return HTMLResponse("Unknown client_id", status_code=400)
    if not _redirect_uri_ok(client, redirect_uri):
        return HTMLResponse("Invalid redirect_uri", status_code=400)

    def redirect_error(error: str) -> Response:
        query = {"error": error}
        if params.get("state"):
            query["state"] = params["state"]
        sep = "&" if "?" in redirect_uri else "?"
        return RedirectResponse(redirect_uri + sep + urlencode(query), status_code=302)

    if params.get("response_type") != "code":
        return redirect_error("unsupported_response_type")
    if not params.get("code_challenge") or params.get("code_challenge_method") != "S256":
        return redirect_error("invalid_request")

    password = params.get("password", "")
    if request.method == "GET" or not (
            AUTH_TOKEN and secrets.compare_digest(password, AUTH_TOKEN)):
        error = ""
        if request.method == "POST":
            error = '<p class="err">Falsches Passwort.</p>'
        hidden = "\n".join(
            f'<input type="hidden" name="{k}" value="{html.escape(params[k])}">'
            for k in _AUTHORIZE_PARAMS if k in params
        )
        page = _LOGIN_PAGE.format(
            client=html.escape(client.get("client_name") or client["client_id"]),
            host=html.escape(urlsplit(redirect_uri).hostname or ""),
            error=error, hidden=hidden,
        )
        return HTMLResponse(page, status_code=401 if error else 200,
                            headers={"X-Frame-Options": "DENY"})

    code = secrets.token_urlsafe(32)
    _auth_codes[code] = {
        "client_id": client["client_id"],
        "redirect_uri": redirect_uri,
        "code_challenge": params["code_challenge"],
        "scope": params.get("scope", "mcp"),
        "expires_at": time.time() + AUTH_CODE_TTL,
    }
    query = {"code": code}
    if params.get("state"):
        query["state"] = params["state"]
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(redirect_uri + sep + urlencode(query), status_code=302)


def _issue_tokens(client_id: str, scope: str) -> Response:
    now = time.time()
    access_token = secrets.token_urlsafe(32)
    refresh_token = secrets.token_urlsafe(32)
    _state["access_tokens"][_hash(access_token)] = {
        "client_id": client_id, "expires_at": now + ACCESS_TOKEN_TTL,
    }
    _state["refresh_tokens"][_hash(refresh_token)] = {
        "client_id": client_id, "scope": scope,
        "expires_at": now + REFRESH_TOKEN_TTL,
    }
    _save_state()
    return JSONResponse({
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": ACCESS_TOKEN_TTL,
        "refresh_token": refresh_token,
        "scope": scope,
    }, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


def _client_id_from(request: Request, form) -> str:
    client_id = form.get("client_id", "")
    auth = request.headers.get("authorization", "")
    if not client_id and auth.lower().startswith("basic "):
        try:
            decoded = base64.b64decode(auth[6:]).decode()
            client_id = decoded.split(":", 1)[0]
        except (ValueError, UnicodeDecodeError):
            pass
    return str(client_id)


async def token_endpoint(request: Request) -> Response:
    form = await request.form()
    grant_type = form.get("grant_type")
    client_id = _client_id_from(request, form)
    if client_id not in _state["clients"]:
        return _oauth_error("invalid_client", status=401)

    if grant_type == "authorization_code":
        entry = _auth_codes.pop(str(form.get("code", "")), None)
        if (not entry or entry["expires_at"] < time.time()
                or entry["client_id"] != client_id
                or entry["redirect_uri"] != form.get("redirect_uri")):
            return _oauth_error("invalid_grant")
        verifier = str(form.get("code_verifier", ""))
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        if not verifier or not secrets.compare_digest(challenge, entry["code_challenge"]):
            return _oauth_error("invalid_grant", "PKCE verification failed")
        return _issue_tokens(client_id, entry["scope"])

    if grant_type == "refresh_token":
        key = _hash(str(form.get("refresh_token", "")))
        entry = _state["refresh_tokens"].get(key)
        if (not entry or entry["expires_at"] < time.time()
                or entry["client_id"] != client_id):
            return _oauth_error("invalid_grant")
        del _state["refresh_tokens"][key]  # rotate
        return _issue_tokens(client_id, entry["scope"])

    return _oauth_error("unsupported_grant_type")


# ---------------------------------------------------------------------------
# MemPalace endpoints
# ---------------------------------------------------------------------------

async def mine_endpoint(request: Request) -> Response:
    if not _check_auth(request):
        return _unauthorized(request)

    form = await request.form()

    wing = str(form.get("wing", "default"))
    wing = _SAFE_NAME.sub("_", wing)[:64]  # sanitize for shell safety

    uploaded = form.getlist("files")
    if not uploaded:
        return JSONResponse({"error": "no files uploaded"}, status_code=400)

    tmpdir = f"/tmp/mine-{wing}-{uuid.uuid4().hex}"
    os.makedirs(tmpdir, exist_ok=True)

    try:
        for upload in uploaded:
            # Sanitize filename — strip any path components
            safe_name = os.path.basename(upload.filename or "file")
            safe_name = _SAFE_NAME.sub("_", safe_name) or "file"
            dest = os.path.join(tmpdir, safe_name)
            content = await upload.read()
            with open(dest, "wb") as fh:
                fh.write(content)

        async with _mine_lock:
            proc = await asyncio.create_subprocess_exec(
                "mempalace", "mine", tmpdir,
                "--mode", "convos",
                "--wing", wing,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await proc.communicate()

        result = {
            "returncode": proc.returncode,
            "wing": wing,
            "files": len(uploaded),
            "stdout": stdout_bytes.decode(errors="replace"),
            "stderr": stderr_bytes.decode(errors="replace"),
        }
        status = 200 if proc.returncode == 0 else 500
        return JSONResponse(result, status_code=status)

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


async def proxy(request: Request) -> Response:
    if not _check_auth(request):
        return _unauthorized(request)

    # Build upstream URL, strip ?token= before forwarding
    path = request.url.path or "/"
    upstream_url = UPSTREAM + path
    forwarded_params = {
        k: v for k, v in request.query_params.items() if k != "token"
    }
    if forwarded_params:
        upstream_url += "?" + urlencode(forwarded_params)

    forward_headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in _HOP_BY_HOP
    }

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
    Route("/.well-known/oauth-protected-resource",
          protected_resource_metadata, methods=["GET"]),
    Route("/.well-known/oauth-protected-resource/{path:path}",
          protected_resource_metadata, methods=["GET"]),
    Route("/.well-known/oauth-authorization-server",
          authorization_server_metadata, methods=["GET"]),
    Route("/.well-known/oauth-authorization-server/{path:path}",
          authorization_server_metadata, methods=["GET"]),
    Route("/register", register_endpoint, methods=["POST"]),
    Route("/authorize", authorize_endpoint, methods=["GET", "POST"]),
    Route("/token", token_endpoint, methods=["POST"]),
    Route("/mine", mine_endpoint, methods=["POST"]),
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
    # proxy_headers: trust X-Forwarded-Proto/Host from the TLS reverse proxy
    # so derived OAuth URLs use https:// when PUBLIC_URL is not set
    uvicorn.run(app, host="0.0.0.0", port=8080,
                proxy_headers=True, forwarded_allow_ips="*")
