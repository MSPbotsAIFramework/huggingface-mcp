"""Unit tests for GatewayCredentialsMiddleware (AC10, AC11; TC-HGF-020, TC-HGF-021).

Exercises the real Starlette app end to end over an in-process ASGI
transport -- no network, no real server socket -- so these tests cover the
exact code path a Gateway request takes, not a hand-rolled approximation of
it.
"""

import asyncio
import json

import httpx
import respx

from huggingface_mcp.__main__ import build_app
from huggingface_mcp.config import Settings
from huggingface_mcp.server import create_mcp_server

from .conftest import lifespan_running

MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


def _make_app():
    settings = Settings()
    mcp = create_mcp_server(settings)
    return build_app(mcp, settings)


def _rpc_result(resp: httpx.Response) -> dict:
    """Parse a streamable-HTTP /mcp response body, which may be plain JSON
    or a single SSE `data:` event depending on the session manager's choice
    for this response -- both carry the same JSON-RPC envelope."""
    body = resp.text
    if body.lstrip().startswith("event:") or body.lstrip().startswith("data:"):
        line = next(ln for ln in body.splitlines() if ln.startswith("data:"))
        body = line[len("data:") :].strip()
    return json.loads(body)["result"]


async def test_missing_header_returns_401_before_any_outbound_call():
    """TC-HGF-020 / AC10."""
    app = _make_app()
    with respx.mock(assert_all_called=False) as mock:
        hub_route = mock.get(url__startswith="https://huggingface.co/").mock(
            return_value=httpx.Response(200, json=[])
        )
        async with lifespan_running(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
                resp = await http.post(
                    "/mcp",
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {"name": "huggingface_whoami", "arguments": {}},
                    },
                    headers=MCP_HEADERS,
                )

        assert not hub_route.called

    assert resp.status_code == 401
    body = resp.json()
    assert body["required_headers"] == ["X-Huggingface-Token"]


async def test_health_endpoint_does_not_require_credentials():
    app = _make_app()
    async with lifespan_running(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
            resp = await http.get("/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_concurrent_requests_with_different_tokens_never_cross():
    """TC-HGF-021 / AC11: two concurrent huggingface_whoami calls, two
    tokens, each response must only ever reflect its own token's identity.
    """
    app = _make_app()

    async def call_whoami(http: httpx.AsyncClient, token: str):
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "huggingface_whoami", "arguments": {}},
        }
        return await http.post(
            "/mcp", json=body, headers={**MCP_HEADERS, "X-Huggingface-Token": token}
        )

    async with lifespan_running(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
            with respx.mock(assert_all_called=False) as mock:
                mock.get("https://huggingface.co/api/whoami-v2").mock(
                    side_effect=lambda request: httpx.Response(
                        200,
                        json={
                            "name": "tenant-a",
                            "fullname": "Tenant A",
                            "type": "user",
                            "orgs": [],
                        }
                        if request.headers.get("authorization") == "Bearer token-a"
                        else {
                            "name": "tenant-b",
                            "fullname": "Tenant B",
                            "type": "user",
                            "orgs": [],
                        },
                    )
                )

                resp_a, resp_b = await asyncio.gather(
                    call_whoami(http, "token-a"), call_whoami(http, "token-b")
                )

    text_a = _rpc_result(resp_a)["content"][0]["text"]
    text_b = _rpc_result(resp_b)["content"][0]["text"]
    assert '"name":"tenant-a"' in text_a
    assert '"name":"tenant-b"' in text_b
    assert "tenant-b" not in text_a
    assert "tenant-a" not in text_b
