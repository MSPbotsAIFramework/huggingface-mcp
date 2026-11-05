"""Hugging Face repo "like" tool -- the only write tool in v1 (AC9;
TC-HGF-018, TC-HGF-019; docs/tickets/PRD-21327.md Sec Decisions made by
DEV01, item 5).
"""

from collections.abc import Callable
from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from ..client import HuggingFaceClient, HuggingFaceError
from ..errors import dump_json
from ._common import NO_TOKEN

RepoType = Literal["model", "dataset", "space"]

_REPO_TYPE_PATH = {"model": "models", "dataset": "datasets", "space": "spaces"}


def register(mcp: FastMCP, client_factory: Callable[[], HuggingFaceClient | None]) -> None:
    @mcp.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=True))
    async def huggingface_like_repo(
        repo_id: Annotated[str, Field(description='Repo id to like, e.g. "org/name".')],
        repo_type: Annotated[
            RepoType, Field(description='Which kind of repo: "model", "dataset", or "space".')
        ],
    ) -> str:
        """Like a model, dataset, or Space for the token's own identity.

        Idempotent: calling it again on an already-liked repo does not error
        and does not create a duplicate like. Returns not_found for a
        nonexistent repo_id."""
        client = client_factory()
        if client is None:
            return NO_TOKEN

        path_segment = _REPO_TYPE_PATH[repo_type]
        try:
            await client.hub_post(f"/api/{path_segment}/{repo_id}/like")
        except HuggingFaceError as exc:
            return exc.to_envelope()

        return dump_json({"liked": True})
