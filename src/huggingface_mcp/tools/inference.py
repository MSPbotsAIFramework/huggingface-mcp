"""Hugging Face Inference Providers chat-completion tool (AC8; TC-HGF-015..017).

Targets the OpenAI-compatible chat-completions route on the Inference
Providers router (router.huggingface.co), not the older single-backend
Serverless Inference API -- see docs/tickets/PRD-21327.md Sec Option analysis.
This is the only tool in this service that executes a model rather than
describing one.
"""

from collections.abc import Callable
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from ..client import HuggingFaceClient, HuggingFaceError
from ..errors import dump_json
from ._common import NO_TOKEN


def register(mcp: FastMCP, client_factory: Callable[[], HuggingFaceClient | None]) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False)
    )
    async def huggingface_run_inference(
        model: Annotated[
            str,
            Field(
                description='Provider-qualified model id, e.g. "meta-llama/Llama-3.1-8B-Instruct".'
            ),
        ],
        messages: Annotated[
            list[dict[str, str]],
            Field(
                description='Chat messages: [{"role": "user"|"system"|"assistant", "content": ""}].'
            ),
        ],
        max_tokens: Annotated[
            int | None,
            Field(description="Max tokens to generate. Omit for the provider's default."),
        ] = None,
        temperature: Annotated[
            float | None,
            Field(description="Sampling temperature. Omit for the provider's default."),
        ] = None,
    ) -> str:
        """Run a chat-completion request against a Hugging Face model via
        the Inference Providers router.

        Not idempotent -- model output can vary between calls. Maps upstream
        failures to upstream_error (5xx or model still loading, retryable),
        rate_limited (429, retryable), or invalid_argument (other 4xx)."""
        client = client_factory()
        if client is None:
            return NO_TOKEN

        body: dict = {"model": model, "messages": messages}
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if temperature is not None:
            body["temperature"] = temperature

        try:
            result = await client.router_post("/chat/completions", body)
        except HuggingFaceError as exc:
            return exc.to_envelope()

        choices = result.get("choices") or []
        first = choices[0] if choices else {}
        message = first.get("message") or {}
        payload = {
            "text": message.get("content"),
            "finish_reason": first.get("finish_reason"),
            "usage": result.get("usage"),
        }
        return dump_json(payload)
