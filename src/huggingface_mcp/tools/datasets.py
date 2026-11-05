"""Hugging Face Hub dataset tools (AC3; TC-HGF-006, TC-HGF-007).

Symmetric to tools/models.py -- see that module for the shared rationale.
"""

from collections.abc import Callable
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from ..client import HuggingFaceClient, HuggingFaceError
from ..errors import dump_json, error_envelope
from ._common import DEFAULT_LIMIT, MAX_LIMIT, NO_TOKEN, capped_limit


def _dataset_summary(d: dict) -> dict:
    return {
        "id": d.get("id"),
        "author": d.get("author"),
        "downloads": d.get("downloads"),
        "likes": d.get("likes"),
        "tags": d.get("tags") or [],
        "last_modified": d.get("lastModified"),
    }


def register(mcp: FastMCP, client_factory: Callable[[], HuggingFaceClient | None]) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
    async def huggingface_search_datasets(
        query: Annotated[
            str | None,
            Field(description="Free-text search, e.g. dataset name/keyword. Omit for trending."),
        ] = None,
        author: Annotated[
            str | None, Field(description="Filter by the owning user or organization.")
        ] = None,
        limit: Annotated[
            int, Field(description=f"Max results (default {DEFAULT_LIMIT}, hard cap {MAX_LIMIT}).")
        ] = DEFAULT_LIMIT,
    ) -> str:
        """Search Hugging Face Hub datasets by name or author.

        Returns id/author/downloads/likes/tags/last_modified for each match.
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
        if author:
            params["author"] = author

        try:
            result = await client.hub_get("/api/datasets", params=params)
        except HuggingFaceError as exc:
            return exc.to_envelope()

        items = result if isinstance(result, list) else []
        has_more = len(items) > capped
        items = items[:capped]
        return dump_json({"items": [_dataset_summary(d) for d in items], "has_more": has_more})

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
    async def huggingface_get_dataset(
        repo_id: Annotated[str, Field(description='Dataset repo id, e.g. "imdb" or "org/name".')],
        revision: Annotated[
            str | None,
            Field(description="Git revision (branch/tag/sha); omit for the default branch."),
        ] = None,
    ) -> str:
        """Get a Hugging Face dataset's card metadata: tags, license, and
        siblings count.

        Returns not_found if repo_id does not exist, or is private and
        inaccessible with the given token."""
        client = client_factory()
        if client is None:
            return NO_TOKEN

        path = f"/api/datasets/{repo_id}" + (f"/revision/{revision}" if revision else "")
        try:
            result = await client.hub_get(path)
        except HuggingFaceError as exc:
            return exc.to_envelope()

        card_data = result.get("cardData") or {}
        payload = {
            "id": result.get("id"),
            "author": result.get("author"),
            "tags": result.get("tags") or [],
            "license": card_data.get("license") or result.get("license"),
            "siblings_count": len(result.get("siblings") or []),
            "gated": bool(result.get("gated", False)),
        }
        return dump_json(payload)
