"""Hugging Face Hub repo file-tree tool (AC7; TC-HGF-013, TC-HGF-014)."""

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
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
    async def huggingface_list_repo_files(
        repo_id: Annotated[
            str, Field(description='Repo id, e.g. "bert-base-uncased" or "org/name".')
        ],
        repo_type: Annotated[
            RepoType, Field(description='Which kind of repo: "model", "dataset", or "space".')
        ],
        revision: Annotated[
            str | None,
            Field(description='Git revision (branch, tag, or commit sha). Defaults to "main".'),
        ] = None,
    ) -> str:
        """List a repo's file tree: path and size for every file.

        Returns not_found for an unknown repo_id or revision."""
        client = client_factory()
        if client is None:
            return NO_TOKEN

        path_segment = _REPO_TYPE_PATH[repo_type]
        rev = revision or "main"
        try:
            result = await client.hub_get(
                f"/api/{path_segment}/{repo_id}/tree/{rev}", params={"recursive": "true"}
            )
        except HuggingFaceError as exc:
            return exc.to_envelope()

        entries = result if isinstance(result, list) else []
        files = [
            {"path": e.get("path"), "size": e.get("size")}
            for e in entries
            if isinstance(e, dict) and e.get("type") == "file"
        ]
        return dump_json({"files": files})
