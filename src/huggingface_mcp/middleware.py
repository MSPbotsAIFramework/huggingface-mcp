import contextvars
import json
import logging
import time
import uuid

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .client import HuggingFaceClient
from .config import Settings

# Per-request credential contextvar (SOP §3.3). Set by
# GatewayCredentialsMiddleware before the MCP handler runs and reset in
# `finally`. asyncio copies the context per task, so concurrent requests from
# different tenants never observe each other's value here -- this is the
# mechanism AC11/TC-HGF-021 depends on.
_token_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "huggingface_token", default=None
)

# One structured (JSON) record per /mcp request (SOP §6; design
# Non-functional, docs/tickets/PRD-21327.md: "structured request logs: tool
# name, status, latency, no header values"). Deliberately limited to
# request-id/tool/status/latency -- never header values, token content, or
# tool arguments/results, which may carry per-tenant business data.
_access_log = logging.getLogger("huggingface_mcp.access")


def get_client_from_context() -> HuggingFaceClient | None:
    """Build a fresh HuggingFaceClient from the current request's token, or
    None if no token is bound (should not normally happen on /mcp -- the
    middleware already 401s first -- but tools defend against it
    independently rather than trusting the middleware unconditionally)."""
    token = _token_var.get()
    if not token:
        return None
    return HuggingFaceClient(token)


def _extract_tool_name(body: bytes) -> str | None:
    """Best-effort JSON-RPC method/tool-name sniff, logging only.

    Never raises: a non-JSON, non-object, or otherwise unexpected body just
    means the log line has tool=None. This must never affect request
    handling, so failures are swallowed rather than surfaced.
    """
    if not body:
        return None
    try:
        payload = json.loads(body)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    method = payload.get("method")
    if method == "tools/call":
        params = payload.get("params")
        if isinstance(params, dict) and isinstance(params.get("name"), str):
            return params["name"]
        return "tools/call"
    return method if isinstance(method, str) else None


class GatewayCredentialsMiddleware:
    """ASGI middleware: reads X-Huggingface-Token and stores it in a
    ContextVar for the duration of the request; also emits one structured
    access-log record per /mcp request (SOP §6).

    Missing header on an /mcp request -> 401 naming the required header,
    before any outbound call to Hugging Face is attempted (SOP §3.2, AC10).
    There is no fallback to an environment variable anywhere in this service
    (SOP §3.1) -- a request with no header simply has no credential
    available.
    """

    def __init__(self, app: ASGIApp, settings: Settings):
        self.app = app
        self.settings = settings
        self._header_name = settings.credential_header

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not path.startswith("/mcp"):
            await self.app(scope, receive, send)
            return

        request_id = uuid.uuid4().hex[:12]
        started_at = time.monotonic()
        request = Request(scope)
        # Header lookup is case-insensitive in Starlette.
        token = request.headers.get(self._header_name.lower())

        if not token:
            response = JSONResponse(
                {
                    "error": "Missing credentials",
                    "message": f"Missing required header: {self._header_name}",
                    "required_headers": [self._header_name],
                },
                status_code=401,
            )
            await response(scope, receive, send)
            self._log(request_id, None, 401, started_at)
            return

        # Only POST carries a JSON-RPC body worth sniffing for a tool name --
        # the Streamable HTTP SSE/GET side has none. The body is drained once
        # and replayed as a single message on the *first* downstream call
        # only; every call after that falls through to the real receive()
        # unchanged. Replaying forever (instead of falling through) was
        # tried first and hung every test: the session manager's disconnect
        # watcher keeps calling receive() for the lifetime of the request
        # and needs a real `http.disconnect`, which only the original
        # channel can ever produce. This never touches header values or the
        # response body (tool arguments/results are not logged, only the
        # method/tool name).
        tool_name: str | None = None
        downstream_receive = receive
        if scope.get("method") == "POST":
            body = await self._drain(receive)
            tool_name = _extract_tool_name(body)
            replayed = False

            async def downstream_receive() -> Message:
                nonlocal replayed
                if not replayed:
                    replayed = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()

        status_holder: dict[str, int] = {}

        async def capturing_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
            await send(message)

        ctx_token = _token_var.set(token)
        try:
            await self.app(scope, downstream_receive, capturing_send)
        finally:
            _token_var.reset(ctx_token)
            self._log(request_id, tool_name, status_holder.get("status"), started_at)

    @staticmethod
    async def _drain(receive: Receive) -> bytes:
        """Read a request body to completion as plain bytes.

        Bounded by the size of a single JSON-RPC call (this service never
        accepts uploads), so buffering the whole body in memory is safe.
        """
        chunks: list[bytes] = []
        more_body = True
        while more_body:
            message = await receive()
            if message["type"] != "http.request":
                break
            chunks.append(message.get("body", b""))
            more_body = message.get("more_body", False)
        return b"".join(chunks)

    @staticmethod
    def _log(request_id: str, tool_name: str | None, status: int | None, started_at: float) -> None:
        latency_ms = round((time.monotonic() - started_at) * 1000, 1)
        _access_log.info(
            json.dumps(
                {
                    "event": "mcp_request",
                    "request_id": request_id,
                    "tool": tool_name,
                    "status": status,
                    "latency_ms": latency_ms,
                }
            )
        )
