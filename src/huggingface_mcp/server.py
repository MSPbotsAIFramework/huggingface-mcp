from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .client import HuggingFaceClient
from .config import Settings
from .middleware import get_client_from_context

SERVICE_INSTRUCTIONS = (
    "Hugging Face Hub + Inference Providers: discover and run models, "
    "datasets, and Spaces with a user's own access token. Typical flow: "
    "huggingface_search_models / huggingface_search_datasets / "
    "huggingface_search_spaces to find a repo_id, then "
    "huggingface_get_model / huggingface_get_dataset / huggingface_get_space "
    "for its card metadata. huggingface_whoami and "
    "huggingface_list_owned_repos cover the token owner's own identity and "
    "repos. huggingface_run_inference sends a chat-completion request to "
    "the Inference Providers router for a given model -- the only tool "
    "that actually executes a model rather than describing one. "
    "huggingface_like_repo is the only write tool, and is idempotent. "
    "huggingface_list_repo_files lists a repo's file tree. No repo "
    "creation, upload, or training-job management in this version."
)


def create_mcp_server(settings: Settings) -> FastMCP:
    """Build the FastMCP server instance and register all tools.

    `stateless_http=True` is required (not just "no session stickiness") --
    the MCP Python SDK's default session manager rejects any request that
    doesn't start with an `initialize` handshake (SOP §1.3). DNS-rebinding
    protection is disabled because this service is never exposed directly to
    the internet; it only ever receives proxied calls from the Gateway
    (SOP §7).
    """
    mcp = FastMCP(
        name="huggingface-mcp",
        instructions=SERVICE_INSTRUCTIONS,
        stateless_http=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    def client_factory() -> HuggingFaceClient | None:
        return get_client_from_context()

    from .tools import datasets, inference, models, repo_files, social, spaces, user

    models.register(mcp, client_factory)
    datasets.register(mcp, client_factory)
    spaces.register(mcp, client_factory)
    user.register(mcp, client_factory)
    repo_files.register(mcp, client_factory)
    social.register(mcp, client_factory)
    inference.register(mcp, client_factory)

    return mcp
