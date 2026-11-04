# huggingface-mcp

Hugging Face Hub + Inference Providers MCP server for the MSPbots Agent platform --
exposes Hub discovery (models, datasets, Spaces), identity, owned-repo listing, file
listing, one idempotent like, and chat-style inference via the Inference Providers
router.

**Tech stack:** Python 3.12 + uv + the official `mcp` SDK's FastMCP (Starlette/uvicorn)

Related requirement: PRD-21327 ([Developer Tools] Hugging Face). See
`docs/tickets/PRD-21327.md` for the full product and technical design.

**Out of scope (v1):** repo creation, file/model upload, training-job management
(AutoTrain), Spaces compute management, webhook/OAuth-app management, and
billing/usage/quota endpoints -- see `docs/tickets/PRD-21327.md` § Non-goals and
§ Decisions made by DEV01 for why each is deferred rather than refused.

<div align="center">

## MSPbots Connector

**The MCP Gateway built for MSPs: visible, controlled, and your data stays where it belongs.**

[**Get started at connect.mspbots.ai →**](https://connect.mspbots.ai)

</div>

---

MSPbots Connector gives your AI clients one MCP URL to reach your entire MSP stack (RMM, PSA, documentation, security, finance), and gives you control over who can call which tool and a record of everything that was called.

**Hosted service: [connect.mspbots.ai](https://connect.mspbots.ai)** · Free to use.

### Why Connector

![MSPbots Connector](https://stgoss.mspbots.ai/files/mcp/mspbots-mcp.png)

| | |
|---|---|
| **Visible** | Every tool call is logged: who, which vendor, when. Usage dashboard, CSV export, SIEM streaming. |
| **Controlled** | Role-based, per-tool allowlist. Deny by default. |
| **Protected** | Credentials encrypted per record. AI clients never see your vendor passwords or tokens. |
| **MSP-native** | Built for the MSP stack only: RMM, PSA, documentation, security, finance. |

### Quick start

1. **Sign up** at [connect.mspbots.ai](https://connect.mspbots.ai).
2. **Connect your vendors.** Pick from 150+ connectors and authorize each one on the vendor's own page, or bring your own API keys. No extra vendor accounts needed.
3. **Set access.** Create roles and decide which tools each role may call. Nothing is allowed until you allow it.
4. **Add your MCP URL to your AI client.** Copy the URL from your Connector dashboard into Claude, ChatGPT, Cursor, VS Code, Gemini, Codex, or any MCP-capable client.
5. **Ask your AI to do real work**, and watch every call appear in the audit log.

### Features

**Unified gateway**
- One MCP URL for all your AI clients
- 150+ connectors across the MSP stack
- Custom MCP servers supported, free
- Authorization happens on each vendor's own page; the AI never sees passwords or tokens
- Bring your own credentials: use the vendor API keys you already have
- Multiple accounts per app; connect once, reuse across AI clients
- Tool search: finds the right action for the task
- One sign-in; vendor credentials are injected behind the scenes and OAuth tokens refresh automatically

**Visibility**
- Audit log for every tool call (who, which vendor, when)
- Usage dashboard: call volume, top vendors, error rate, per-user activity
- Export audit logs to CSV, or stream to Loki, Graylog, or LogScale (SIEM)
- Optional recording of tool arguments and result summaries (off by default, enabled per organization)

**Control**
- Per-role, per-tool allowlist, deny by default
- owner / admin / member roles
- SCIM provisioning with Entra ID, Okta, JumpCloud, and Google Workspace

**Security**
- Credentials encrypted one record at a time (AES-256-GCM, per-record key)
- AI clients authenticate with OAuth 2.1 + PKCE
- Vendor responses are scanned for prompt-injection patterns and returned as structured data. This is one layer of defense, not a guarantee against every attack.
- Static egress IP for customer firewall and API allowlisting

### Pricing

**Free.**

Links: [connect.mspbots.ai](https://connect.mspbots.ai) · [mspbots.ai](https://mspbots.ai)

---

## Authentication headers

This service is **stateless**: it never stores, caches, or logs a tenant's Hugging Face
token. Every request must carry one header:

| Header | Type | Required | Description | Example |
|---|---|---|---|---|
| `X-Huggingface-Token` | string | Yes | Hugging Face user access token (`hf_xxx`), created at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens). "Read" scope is sufficient for every tool except `huggingface_like_repo`, which needs "Write". | `hf_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` |

Missing the header on a `/mcp` request returns `401` with a `required_headers` list, and
the service never calls Hugging Face. There is no environment-variable fallback
anywhere in this service (SOP §3.1).

## Quick Start

```bash
cd huggingface-mcp
uv sync
MCP_HTTP_PORT=8080 uv run huggingface-mcp
curl http://localhost:8080/health
```

This service only runs as an HTTP server (`POST /mcp`, Streamable HTTP) -- there is no
stdio transport and no single-tenant "env mode"; every tool call carries its own
credential header (see Authentication headers above).

## Tool List

| Tool | Description | Parameters |
|---|---|---|
| `huggingface_search_models` | Search Hub models by name, task, library, or author | `query`, `task`, `library`, `author` (all optional), `limit` (optional, default 20, hard cap 100) |
| `huggingface_get_model` | Get a model's card metadata | `repo_id` (required), `revision` (optional) |
| `huggingface_search_datasets` | Search Hub datasets by name or author | `query`, `author` (optional), `limit` (optional, default 20, hard cap 100) |
| `huggingface_get_dataset` | Get a dataset's card metadata | `repo_id` (required), `revision` (optional) |
| `huggingface_search_spaces` | Search Hub Spaces by name, SDK, or author | `query`, `sdk`, `author` (optional), `limit` (optional, default 20, hard cap 100) |
| `huggingface_get_space` | Get a Space's metadata (sdk, runtime stage) | `repo_id` (required) |
| `huggingface_whoami` | Get the identity bound to the current token | None |
| `huggingface_list_owned_repos` | List repos owned by a user or org | `repo_type` (required: `model`/`dataset`/`space`), `owner` (optional, defaults to self), `limit` (optional) |
| `huggingface_list_repo_files` | List a repo's file tree | `repo_id` (required), `repo_type` (required), `revision` (optional, default `main`) |
| `huggingface_like_repo` | Like a model, dataset, or Space (idempotent) | `repo_id` (required), `repo_type` (required) |
| `huggingface_run_inference` | Chat-completion via the Inference Providers router | `model` (required), `messages` (required), `max_tokens`, `temperature` (optional) |

**No repo creation, upload, or training-job management exists in v1** -- see Known
Limitations.

## Test Examples

### tools/list
```bash
curl -X POST http://localhost:8080/mcp \
  -H "X-Huggingface-Token: hf_xxx" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","method":"tools/list","id":1}'
```

### tools/call -- get a model
```bash
curl -X POST http://localhost:8080/mcp \
  -H "X-Huggingface-Token: hf_xxx" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{
    "jsonrpc": "2.0",
    "method": "tools/call",
    "id": 2,
    "params": {
      "name": "huggingface_get_model",
      "arguments": {"repo_id": "bert-base-uncased"}
    }
  }'
```

### tools/call -- run inference
```bash
curl -X POST http://localhost:8080/mcp \
  -H "X-Huggingface-Token: hf_xxx" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{
    "jsonrpc": "2.0",
    "method": "tools/call",
    "id": 3,
    "params": {
      "name": "huggingface_run_inference",
      "arguments": {
        "model": "meta-llama/Llama-3.1-8B-Instruct",
        "messages": [{"role": "user", "content": "Say hi in one word"}]
      }
    }
  }'
```

### Missing header -> 401
```bash
curl -i -X POST http://localhost:8080/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","method":"tools/list","id":1}'
# HTTP/1.1 401 Unauthorized
# {"error":"Missing credentials","message":"Missing required header: X-Huggingface-Token","required_headers":["X-Huggingface-Token"]}
```

## API Reference

- [Hugging Face Hub API docs](https://huggingface.co/docs/hub/api)
- [Hub API OpenAPI spec](https://huggingface.co/.well-known/openapi.json)
- [Inference Providers overview](https://huggingface.co/docs/inference-providers)
- [Access tokens](https://huggingface.co/docs/hub/security-tokens)

This service is a clean-room implementation against Hugging Face's own public API
docs, not a fork or port of any third-party Hugging Face client library.

## Known Limitations

- **No repo creation, file/model upload, or training-job management (AutoTrain) in
  v1** -- Hugging Face's write surface for repo content is fundamentally git/LFS-based
  and does not map onto a single stateless HTTP tool call; see
  `docs/tickets/PRD-21327.md` Sec Decisions made by DEV01 for the reasoning and how to
  request it as a fast follow.
- **`huggingface_run_inference` covers only the OpenAI-compatible chat-completions
  route** -- non-chat task types (text-classification, embeddings, etc.) are not wired
  in v1; see Sec Open questions, Q1.
- **No test Hugging Face account available at build time** -- `huggingface_whoami`,
  `huggingface_list_owned_repos`, `huggingface_run_inference` end to end, and
  `huggingface_like_repo` end to end are marked "needs a real vendor account" in the
  self-test report rather than executed against a live account.
- **`huggingface_list_owned_repos` has no native upstream pagination beyond
  `limit`/`author`** -- a very large account's repo count is not verified against a
  real large account (no test account available).
