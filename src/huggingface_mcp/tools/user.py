"""Hugging Face identity tools (AC5, AC6; TC-HGF-010..012).

huggingface_list_owned_repos resolves owner to the token's own identity via
huggingface_whoami's endpoint when omitted -- a second Hub call, not cached
across requests (SOP §3.4: no cross-request caching of derived identity).
"""

from collections.abc import Callable
from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from ..client import HuggingFaceClient, HuggingFaceError
from ..errors import dump_json
from ._common import DEFAULT_LIMIT, MAX_LIMIT, NO_TOKEN, capped_limit

RepoType = Literal["model", "dataset", "space"]

_REPO_TYPE_PATH = {"model": "models", "dataset": "datasets", "space": "spaces"}


def register(mcp: FastMCP, client_factory: Callable[[], HuggingFaceClient | None]) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
    async def huggingface_whoami() -> str:
        """Get the identity bound to the current Hugging Face token.

        Returns name, fullname, account type (user or org), and the names
        of organizations the user belongs to -- no billing or plan data."""
        client = client_factory()
        if client is None:
            return NO_TOKEN

        try:
            result = await client.hub_get("/api/whoami-v2")
        except HuggingFaceError as exc:
            return exc.to_envelope()

        orgs = [o.get("name") for o in (result.get("orgs") or []) if isinstance(o, dict)]
        payload = {
            "name": result.get("name"),
            "fullname": result.get("fullname"),
            "type": result.get("type"),
            "orgs": orgs,
        }
        return dump_json(payload)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
    async def huggingface_list_owned_repos(
        repo_type: Annotated[
            RepoType,
            Field(description='Which kind of repo to list: "model", "dataset", or "space".'),
        ],
        owner: Annotated[
            str | None,
            Field(
                description="User or org to list repos for. Omit to use the token's own identity."
            ),
        ] = None,
        limit: Annotated[
            int, Field(description=f"Max results (default {DEFAULT_LIMIT}, hard cap {MAX_LIMIT}).")
        ] = DEFAULT_LIMIT,
    ) -> str:
        """List repos (models/datasets/spaces) owned by a user or org.

        Defaults owner to the token's own identity. An owner with zero repos
        of the given repo_type returns an empty items list, not not_found."""
        client = client_factory()
        if client is None:
            return NO_TOKEN
        capped = capped_limit(limit)

        if owner is None:
            try:
                me = await client.hub_get("/api/whoami-v2")
            except HuggingFaceError as exc:
                return exc.to_envelope()
            owner = me.get("name")

        path_segment = _REPO_TYPE_PATH[repo_type]
        try:
            result = await client.hub_get(
                f"/api/{path_segment}", params={"author": owner, "limit": capped + 1}
            )
        except HuggingFaceError as exc:
            return exc.to_envelope()

        items = result if isinstance(result, list) else []
        has_more = len(items) > capped
        items = items[:capped]
        summarized = [
            {
                "id": r.get("id"),
                "private": r.get("private"),
                "downloads": r.get("downloads"),
                "likes": r.get("likes"),
            }
            for r in items
        ]
        return dump_json({"items": summarized, "has_more": has_more})
