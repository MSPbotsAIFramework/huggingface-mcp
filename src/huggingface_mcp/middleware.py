import contextvars

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

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


def get_client_from_context() -> HuggingFaceClient | None:
    """Build a fresh HuggingFaceClient from the current request's token, or
    None if no token is bound (should not normally happen on /mcp -- the
    middleware already 401s first -- but tools defend against it
    independently rather than trusting the middleware unconditionally)."""
    token = _token_var.get()
    if not token:
        return None
    return HuggingFaceClient(token)


class GatewayCredentialsMiddleware:
    """ASGI middleware: reads X-Huggingface-Token and stores it in a
    ContextVar for the duration of the request.

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
            return

        ctx_token = _token_var.set(token)
        try:
            await self.app(scope, receive, send)
        finally:
            _token_var.reset(ctx_token)
