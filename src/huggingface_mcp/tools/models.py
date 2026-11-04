"""Hugging Face Hub model tools (AC1, AC2; TC-HGF-001..005).

Tool naming convention: huggingface_<verb>_<resource>.
"""

from collections.abc import Callable
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from ..client import HuggingFaceClient, HuggingFaceError
from ..errors import dump_json, error_envelope
from ._common import DEFAULT_LIMIT, MAX_LIMIT, NO_TOKEN, capped_limit


def _model_summary(m: dict) -> dict:
    return {
        "id": m.get("id") or m.get("modelId"),
        "author": m.get("author"),
        "pipeline_tag": m.get("pipeline_tag"),
        "library_name": m.get("library_name"),
        "downloads": m.get("downloads"),
        "likes": m.get("likes"),
        "tags": m.get("tags") or [],
        "last_modified": m.get("lastModified"),
    }


def register(mcp: FastMCP, client_factory: Callable[[], HuggingFaceClient | None]) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
    async def huggingface_search_models(
        query: Annotated[
            str | None,
            Field(
                description="Free-text search, e.g. model name/keyword. Omit for trending models."
            ),
        ] = None,
        task: Annotated[
            str | None,
            Field(description='Filter by pipeline tag, e.g. "text-generation" or "translation".'),
        ] = None,
        library: Annotated[
            str | None, Field(description='Filter by library, e.g. "transformers", "diffusers".')
        ] = None,
        author: Annotated[
            str | None, Field(description="Filter by the owning user or organization.")
        ] = None,
        limit: Annotated[
            int, Field(description=f"Max results (default {DEFAULT_LIMIT}, hard cap {MAX_LIMIT}).")
        ] = DEFAULT_LIMIT,
    ) -> str:
        """Search Hugging Face Hub models by name, task, library, or author.

        Returns id/author/pipeline_tag/library_name/downloads/likes/tags/
        last_modified for each match. has_more is true if more results exist
        beyond limit."""
        client = client_factory()
        if client is None:
            return NO_TOKEN
        if limit > MAX_LIMIT:
            return error_envelope("invalid_argument", f"limit must be <= {MAX_LIMIT}")
        capped = capped_limit(limit)

        params: dict = {"limit": capped + 1}
        if query:
            params["search"] = query
        if task:
            params["pipeline_tag"] = task
        if library:
            params["filter"] = library
        if author:
            params["author"] = author

        try:
            result = await client.hub_get("/api/models", params=params)
        except HuggingFaceError as exc:
            return exc.to_envelope()

        items = result if isinstance(result, list) else []
        has_more = len(items) > capped
        items = items[:capped]
        return dump_json({"items": [_model_summary(m) for m in items], "has_more": has_more})

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
    async def huggingface_get_model(
        repo_id: Annotated[
            str, Field(description='Model repo id, e.g. "bert-base-uncased" or "org/name".')
        ],
        revision: Annotated[
            str | None,
            Field(description="Git revision (branch/tag/sha); omit for the default branch."),
        ] = None,
    ) -> str:
        """Get a Hugging Face model's card metadata: tags, pipeline_tag,
        library_name, license, siblings count, and whether it is gated.

        Returns not_found if repo_id does not exist, or is private and
        inaccessible with the given token."""
        client = client_factory()
        if client is None:
            return NO_TOKEN

        path = f"/api/models/{repo_id}" + (f"/revision/{revision}" if revision else "")
        try:
            result = await client.hub_get(path)
        except HuggingFaceError as exc:
            return exc.to_envelope()

        card_data = result.get("cardData") or {}
        payload = {
            "id": result.get("id") or result.get("modelId"),
            "author": result.get("author"),
            "tags": result.get("tags") or [],
            "pipeline_tag": result.get("pipeline_tag"),
            "library_name": result.get("library_name"),
            "license": card_data.get("license") or result.get("license"),
            "siblings_count": len(result.get("siblings") or []),
            "gated": bool(result.get("gated", False)),
        }
        return dump_json(payload)
