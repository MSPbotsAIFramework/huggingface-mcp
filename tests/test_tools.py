"""Unit tests for the huggingface_mcp tool layer (TC-HGF-001..009, 013, 014,
and the two mapping cases ac.json asks the dev leg to add alongside
TC-HGF-016/017). The Hugging Face Hub and Inference Providers router are
mocked throughout via respx -- no network call ever leaves this process.
"""

import httpx
import pytest
import respx

from huggingface_mcp.config import Settings
from huggingface_mcp.middleware import _token_var
from huggingface_mcp.server import create_mcp_server

HUB = "https://huggingface.co"
ROUTER = "https://router.huggingface.co/v1"

EXPECTED_TOOL_NAMES = {
    "huggingface_search_models",
    "huggingface_get_model",
    "huggingface_search_datasets",
    "huggingface_get_dataset",
    "huggingface_search_spaces",
    "huggingface_get_space",
    "huggingface_whoami",
    "huggingface_list_owned_repos",
    "huggingface_list_repo_files",
    "huggingface_like_repo",
    "huggingface_run_inference",
}


@pytest.fixture
def mcp():
    return create_mcp_server(Settings())


async def _call_with_token(mcp, token, tool_name, arguments):
    ctx_token = _token_var.set(token)
    try:
        return await mcp.call_tool(tool_name, arguments)
    finally:
        _token_var.reset(ctx_token)


def _text(result) -> str:
    content, _ = result
    return content[0].text


async def test_tools_list_snapshot(mcp):
    tools = await mcp.list_tools()

    names = {t.name for t in tools}
    assert names == EXPECTED_TOOL_NAMES
    assert len(tools) == 11
    for tool in tools:
        assert tool.description, f"{tool.name} is missing a description"
        assert len(tool.description) <= 500, f"{tool.name} description exceeds 500 chars"

    search_tool = next(t for t in tools if t.name == "huggingface_search_models")
    assert search_tool.inputSchema.get("required", []) == []

    get_tool = next(t for t in tools if t.name == "huggingface_get_model")
    assert set(get_tool.inputSchema.get("required", [])) == {"repo_id"}


async def test_whoami_without_token_returns_not_configured():
    """No contextvar token set -> client_factory() must return None before
    any outbound call (mirrors the middleware's own 401, as a defense in
    depth for direct `call_tool` use in tests)."""
    mcp = create_mcp_server(Settings())
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(f"{HUB}/api/whoami-v2")
        result = await mcp.call_tool("huggingface_whoami", {})
        assert not route.called

    text = _text(result)
    assert '"code":"not_configured"' in text


# -- TC-HGF-001..003 (AC1): huggingface_search_models -------------------------


@respx.mock
async def test_search_models_respects_limit_and_sets_has_more(mcp):
    """TC-HGF-001."""
    page = [
        {
            "id": f"model-{i}",
            "author": "someorg",
            "pipeline_tag": "text-generation",
            "library_name": "transformers",
            "downloads": 100 + i,
            "likes": i,
            "tags": ["pytorch"],
            "lastModified": "2026-10-01T00:00:00.000Z",
        }
        for i in range(6)  # limit=5 + 1 to detect has_more
    ]
    route = respx.get(f"{HUB}/api/models", params={"search": "bert", "limit": "6"}).mock(
        return_value=httpx.Response(200, json=page)
    )

    result = await _call_with_token(
        mcp, "tok", "huggingface_search_models", {"query": "bert", "limit": 5}
    )

    assert route.called
    text = _text(result)
    assert '"has_more":true' in text
    import json

    payload = json.loads(text)
    assert len(payload["items"]) == 5
    assert payload["items"][0]["id"] == "model-0"
    assert payload["items"][0]["author"] == "someorg"
    assert payload["items"][0]["downloads"] == 100


@respx.mock
async def test_search_models_default_query_and_limit(mcp):
    """TC-HGF-002."""
    page = [{"id": f"model-{i}"} for i in range(20)]
    route = respx.get(f"{HUB}/api/models", params={"limit": "21"}).mock(
        return_value=httpx.Response(200, json=page)
    )

    result = await _call_with_token(mcp, "tok", "huggingface_search_models", {})

    assert route.called
    sent_params = route.calls.last.request.url.params
    assert "search" not in sent_params
    text = _text(result)
    assert '"has_more":false' in text
    import json

    payload = json.loads(text)
    assert len(payload["items"]) == 20


async def test_search_models_rejects_limit_over_hard_cap(mcp):
    """TC-HGF-003."""
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(f"{HUB}/api/models")
        result = await _call_with_token(mcp, "tok", "huggingface_search_models", {"limit": 500})
        assert not route.called

    text = _text(result)
    assert '"code":"invalid_argument"' in text


# -- TC-HGF-004/005 (AC2): huggingface_get_model ------------------------------


@respx.mock
async def test_get_model_returns_card_fields(mcp):
    """TC-HGF-004."""
    respx.get(f"{HUB}/api/models/bert-base-uncased").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "bert-base-uncased",
                "tags": ["pytorch", "bert"],
                "pipeline_tag": "fill-mask",
                "library_name": "transformers",
                "cardData": {"license": "apache-2.0"},
                "siblings": [{"rfilename": "config.json"}, {"rfilename": "pytorch_model.bin"}],
                "gated": False,
            },
        )
    )

    result = await _call_with_token(
        mcp, "tok", "huggingface_get_model", {"repo_id": "bert-base-uncased"}
    )

    text = _text(result)
    assert '"license":"apache-2.0"' in text
    assert '"siblings_count":2' in text
    assert '"pipeline_tag":"fill-mask"' in text


@respx.mock
async def test_get_model_maps_404_to_not_found(mcp):
    """TC-HGF-005."""
    respx.get(f"{HUB}/api/models/does-not-exist").mock(
        return_value=httpx.Response(404, json={"error": "Repository not found"})
    )

    result = await _call_with_token(
        mcp, "tok", "huggingface_get_model", {"repo_id": "does-not-exist"}
    )

    text = _text(result)
    assert '"code":"not_found"' in text
    assert "500" not in text


# -- TC-HGF-006/007 (AC3): huggingface_search_datasets / huggingface_get_dataset -


@respx.mock
async def test_search_datasets_happy_path(mcp):
    """TC-HGF-006."""
    respx.get(f"{HUB}/api/datasets", params={"search": "imdb", "limit": "21"}).mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": "imdb", "author": "stanfordnlp", "downloads": 500, "likes": 10, "tags": []}
            ],
        )
    )

    result = await _call_with_token(mcp, "tok", "huggingface_search_datasets", {"query": "imdb"})

    text = _text(result)
    assert '"id":"imdb"' in text
    assert '"author":"stanfordnlp"' in text


@respx.mock
async def test_get_dataset_maps_404_to_not_found(mcp):
    """TC-HGF-007."""
    respx.get(f"{HUB}/api/datasets/does-not-exist").mock(
        return_value=httpx.Response(404, json={"error": "Repository not found"})
    )

    result = await _call_with_token(
        mcp, "tok", "huggingface_get_dataset", {"repo_id": "does-not-exist"}
    )

    assert '"code":"not_found"' in _text(result)


# -- TC-HGF-008/009 (AC4): huggingface_search_spaces / huggingface_get_space -----


@respx.mock
async def test_search_spaces_includes_sdk_and_stage(mcp):
    """TC-HGF-008."""
    respx.get(f"{HUB}/api/spaces", params={"search": "gradio", "limit": "21"}).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": "org/demo",
                    "author": "org",
                    "sdk": "gradio",
                    "runtime": {"stage": "RUNNING"},
                    "likes": 3,
                    "tags": [],
                }
            ],
        )
    )

    result = await _call_with_token(mcp, "tok", "huggingface_search_spaces", {"query": "gradio"})

    text = _text(result)
    assert '"sdk":"gradio"' in text
    assert '"stage":"RUNNING"' in text


@respx.mock
async def test_get_space_maps_404_to_not_found(mcp):
    """TC-HGF-009."""
    respx.get(f"{HUB}/api/spaces/does-not-exist").mock(
        return_value=httpx.Response(404, json={"error": "Repository not found"})
    )

    result = await _call_with_token(
        mcp, "tok", "huggingface_get_space", {"repo_id": "does-not-exist"}
    )

    assert '"code":"not_found"' in _text(result)


# -- TC-HGF-013/014 (AC7): huggingface_list_repo_files --------------------------


@respx.mock
async def test_list_repo_files_returns_tree(mcp):
    """TC-HGF-013."""
    respx.get(f"{HUB}/api/models/bert-base-uncased/tree/main", params={"recursive": "true"}).mock(
        return_value=httpx.Response(
            200,
            json=[
                {"type": "file", "path": "config.json", "size": 123},
                {"type": "file", "path": "pytorch_model.bin", "size": 440000000},
                {"type": "directory", "path": "subdir", "size": None},
            ],
        )
    )

    result = await _call_with_token(
        mcp,
        "tok",
        "huggingface_list_repo_files",
        {"repo_id": "bert-base-uncased", "repo_type": "model"},
    )

    text = _text(result)
    assert '"path":"config.json"' in text
    assert '"size":123' in text
    assert "subdir" not in text  # directories are not files


@respx.mock
async def test_list_repo_files_unknown_revision_is_not_found(mcp):
    """TC-HGF-014."""
    respx.get(
        f"{HUB}/api/models/bert-base-uncased/tree/no-such-rev", params={"recursive": "true"}
    ).mock(return_value=httpx.Response(404, json={"error": "Revision not found"}))

    result = await _call_with_token(
        mcp,
        "tok",
        "huggingface_list_repo_files",
        {"repo_id": "bert-base-uncased", "repo_type": "model", "revision": "no-such-rev"},
    )

    assert '"code":"not_found"' in _text(result)


# -- Extra auto cases ac.json asks the dev leg to add (alongside the manual --
# -- TC-HGF-016/017, which need a real vendor account to reproduce live) -----


@respx.mock
async def test_run_inference_maps_503_to_upstream_error(mcp):
    respx.post(f"{ROUTER}/chat/completions").mock(
        return_value=httpx.Response(503, json={"error": "Model is loading", "estimated_time": 20.0})
    )

    result = await _call_with_token(
        mcp,
        "tok",
        "huggingface_run_inference",
        {
            "model": "meta-llama/Llama-3.1-8B-Instruct",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )

    text = _text(result)
    assert '"code":"upstream_error"' in text
    assert '"retryable":true' in text


@respx.mock
async def test_run_inference_maps_429_to_rate_limited(mcp):
    route = respx.post(f"{ROUTER}/chat/completions")
    route.side_effect = [
        httpx.Response(429, json={"error": "rate limited"}, headers={"Retry-After": "0"})
        for _ in range(4)  # exhausts the client's own bounded retry budget
    ]

    result = await _call_with_token(
        mcp,
        "tok",
        "huggingface_run_inference",
        {
            "model": "meta-llama/Llama-3.1-8B-Instruct",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )

    text = _text(result)
    assert '"code":"rate_limited"' in text
    assert '"retryable":true' in text
