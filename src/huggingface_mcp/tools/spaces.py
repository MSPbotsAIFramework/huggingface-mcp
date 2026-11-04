"""Hugging Face Hub Space tools (AC4; TC-HGF-008, TC-HGF-009).

Symmetric to tools/models.py, plus the Space-specific sdk/stage fields.
"""

from collections.abc import Callable
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from ..client import HuggingFaceClient, HuggingFaceError
from ..errors import dump_json, error_envelope
from ._common import DEFAULT_LIMIT, MAX_LIMIT, NO_TOKEN, capped_limit


def _space_summary(s: dict) -> dict:
    runtime = s.get("runtime") or {}
    return {
        "id": s.get("id"),
        "author": s.get("author"),
        "sdk": s.get("sdk"),
        "stage": runtime.get("stage"),
        "likes": s.get("likes"),
        "tags": s.get("tags") or [],
        "last_modified": s.get("lastModified"),
    }


def register(mcp: FastMCP, client_factory: Callable[[], HuggingFaceClient | None]) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
    async def huggingface_search_spaces(
        query: Annotated[
            str | None,
            Field(
                description="Free-text search, e.g. Space name/keyword. Omit for trending Spaces."
            ),
        ] = None,
        sdk: Annotated[
            str | None,
            Field(description='Filter by SDK, e.g. "gradio", "streamlit", "docker", "static".'),
        ] = None,
        author: Annotated[
            str | None, Field(description="Filter by the owning user or organization.")
        ] = None,
        limit: Annotated[
            int, Field(description=f"Max results (default {DEFAULT_LIMIT}, hard cap {MAX_LIMIT}).")
        ] = DEFAULT_LIMIT,
    ) -> str:
        """Search Hugging Face Hub Spaces by name, SDK, or author.

        Returns id/author/sdk/stage/likes/tags/last_modified for each match.
        has_more is true if more results exist beyond limit."""
        client = client_factory()
        if client is None:
            return NO_TOKEN
        if limit > MAX_LIMIT:
            return error_envelope("invalid_argument", f"limit must be <= {MAX_LIMIT}")
        capped = capped_limit(limit)

        params: dict = {"limit": capped + 1}
        if query:
            params["search"] = query
        if sdk:
            params["filter"] = sdk
        if author:
            params["author"] = author

        try:
            result = await client.hub_get("/api/spaces", params=params)
        except HuggingFaceError as exc:
            return exc.to_envelope()

        items = result if isinstance(result, list) else []
        has_more = len(items) > capped
        items = items[:capped]
        return dump_json({"items": [_space_summary(s) for s in items], "has_more": has_more})

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
    async def huggingface_get_space(
        repo_id: Annotated[str, Field(description='Space repo id, e.g. "org/name".')],
    ) -> str:
        """Get a Hugging Face Space's metadata: sdk, runtime stage, and tags.

        stage is e.g. RUNNING, BUILDING, SLEEPING. Returns not_found if
        repo_id does not exist, or is private and inaccessible with the
        given token."""
        client = client_factory()
        if client is None:
            return NO_TOKEN

        try:
            result = await client.hub_get(f"/api/spaces/{repo_id}")
        except HuggingFaceError as exc:
            return exc.to_envelope()

        runtime = result.get("runtime") or {}
        payload = {
            "id": result.get("id"),
            "author": result.get("author"),
            "sdk": result.get("sdk"),
            "stage": runtime.get("stage"),
            "tags": result.get("tags") or [],
            "likes": result.get("likes"),
        }
        return dump_json(payload)
