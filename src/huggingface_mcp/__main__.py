import logging
import sys

from .config import get_settings
from .middleware import GatewayCredentialsMiddleware
from .server import create_mcp_server


def _configure_logging() -> None:
    """Plain `%(message)s` format on stderr (SOP §6): middleware.py already
    emits complete, self-contained JSON lines per request, so the default
    logging prefix (timestamp/level/name) would just produce invalid JSON
    for anything parsing these logs downstream."""
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)


def build_app(mcp, settings):
    """Wrap the FastMCP Starlette app with a local-only /health route.

    `/health` must never depend on the Hugging Face API (SOP §1.1): it is a
    pure liveness probe for the container orchestrator, and Hugging Face
    being slow or down must not cause this service to be restarted.
    """
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Mount, Route

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    mcp_app = mcp.streamable_http_app()  # Starlette app owning the session-manager lifespan
    mounted = GatewayCredentialsMiddleware(mcp_app, settings)

    # Mount() does not run a sub-app's lifespan, so the streamable-http
    # session manager's task group would never start ("Task group is not
    # initialized"). Propagate the MCP app's lifespan to the outer app
    # explicitly (SOP §7).
    return Starlette(
        routes=[Route("/health", health), Mount("/", app=mounted)],
        lifespan=lambda app: mcp_app.router.lifespan_context(app),
    )


def main() -> None:
    _configure_logging()
    settings = get_settings()
    mcp = create_mcp_server(settings)

    import uvicorn

    app = build_app(mcp, settings)
    print(
        f"huggingface-mcp listening on http://{settings.mcp_http_host}:{settings.mcp_http_port}/mcp",
        file=sys.stderr,
    )
    uvicorn.run(app, host=settings.mcp_http_host, port=settings.mcp_http_port)


if __name__ == "__main__":
    main()
