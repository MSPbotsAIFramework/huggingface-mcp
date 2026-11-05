# PRD-21327 · Code review

Status: frozen @ 98f7715

## Round 1 · 2026-11-05 · reviewed b91af56 (base a84765f)

Verdict: changes requested

### Findings

1. [must-fix] `src/huggingface_mcp/middleware.py:47` (service-wide: no log call anywhere in `src/`) — The service has no structured request logging at all. `grep -rn "logging\|getLogger\|print(" src/` finds exactly one hit: a one-time startup banner `print()` in `src/huggingface_mcp/__main__.py:43`. There is no per-request/per-tool-call logging of tool name, status, latency, or a request-id anywhere in `middleware.py`, `client.py`, `server.py`, or any `tools/*.py` module. This contradicts the technical design's own Non-functional section (`docs/tickets/PRD-21327.md:167`: "structured request logs (tool name, status, latency, no header values)") and is a mandatory item in the vendor-mcp-development SOP: §6 "日志与可观测性" ("用结构化日志...记录: 工具名、入参摘要、API 路径与状态码、耗时、错误 code") and the §14 delivery checklist ("[ ] 结构化日志带 request-id；不打印凭据/token/敏感业务数据"). Fix: add structured logging (e.g., the stdlib `logging` module with a JSON formatter, or `structlog`) that emits one record per tool call with a request-id, tool name, outcome/status, and latency, and never logs header values or token content — matching what the design already promised.

### Suggestions

1. `src/huggingface_mcp/client.py:16-120` — The retry/backoff loop has no overall per-call deadline tracking. Each attempt can take up to `connect(5s) + read(30s) ≈ 35s`, and with `_MAX_RETRIES = 3` (4 attempts total) plus up to 3 inter-attempt sleeps, a sustained-read-timeout scenario (the Hugging Face router hanging near the full read timeout on every attempt, which is exactly the kind of upstream slowness §5 timeouts exist to bound) can push total elapsed time to roughly `4×35s + ~7s backoff ≈ 147s`, above the SOP §5 / §14 checklist requirement of "单次调用总耗时（含重试）必须 < 120s". No test exercises this path (all mocked responses in `tests/test_tools.py` return immediately, so the retry loop's sleeps are the only thing actually timed). Not flagged as a must-fix because it requires a specific, less-common failure mode (repeated hard read-timeouts rather than fast 429/503 responses, which are the documented AC8 case and stay well under budget), but worth tightening — e.g. track a monotonic `started_at` across the whole `_request` call and return `upstream_error` once the remaining budget can't fit another attempt, rather than relying solely on per-attempt timeouts.

### Fixes (dev, round 1)

1. **`src/huggingface_mcp/middleware.py` — must-fix #1 (structured request logging).** Added a per-`/mcp`-request structured JSON log record, emitted once per request by `GatewayCredentialsMiddleware.__call__` through a dedicated `huggingface_mcp.access` logger: `request_id` (a locally generated 12-hex-char UUID4, not tied to any upstream id), `tool` (sniffed from the JSON-RPC body's `method`/`params.name` for `POST /mcp` requests only; `None` on the 401-before-dispatch path and on the SSE/GET side, which carries no JSON-RPC body), `status` (captured from the wrapped ASGI `send()`'s `http.response.start` message), and `latency_ms` (`time.monotonic()` delta). This matches the design's own Non-functional wording verbatim ("tool name, status, latency, no header values") — the record never includes header values, token content, or tool arguments/results.
   - The request body is drained once (bounded: this service only ever receives single JSON-RPC calls, never uploads) to sniff the tool name, then replayed to the downstream app as a single `http.request` message on the *first* `receive()` call; every call after that falls through to the real `receive()` unchanged.
   - **A hang found and fixed during self-test**: the first version replayed the buffered body forever instead of falling through, and `uv run pytest -q` hung past the harness's 20-minute timeout on the new logging test. Root cause: FastMCP's Streamable HTTP session manager keeps calling `receive()` for the life of a request to watch for a real `http.disconnect`; a synthetic receive that only ever replays the cached body never produces one, so the watcher spins forever. Fixed by replaying the buffered body exactly once, then delegating every subsequent call to the original `receive()` so disconnect detection still works. Isolated with `timeout 30 uv run pytest -q tests/test_middleware.py::test_tool_call_emits_one_structured_log_record_without_token` before and after the fix (hung → 0.42s).
   - `src/huggingface_mcp/__main__.py`: added `_configure_logging()` (`logging.basicConfig(level=INFO, format="%(message)s", stream=sys.stderr)`), called at the top of `main()`. Without it the `logging` module's default "no handler configured" behavior silently drops every record. Format is bare `%(message)s` because `middleware.py` already emits complete JSON lines; the default timestamp/level prefix would break JSON parsing for anything consuming these logs.
   - Tests added: `tests/test_middleware.py::test_tool_call_emits_one_structured_log_record_without_token` (exactly one record per tool call; correct `tool`/`status`/`latency_ms`; asserts the raw header value never appears anywhere in the serialized record) and `::test_missing_header_logs_401_with_no_tool_name` (401 path logs `status=401, tool=None`).
   - Verified against a running instance, not just the unit tests: started the server locally (`MCP_HTTP_PORT=8099 uv run python -m huggingface_mcp`, to avoid a leftover server from an unrelated ticket already bound to the default port 8080) and drove it with `curl`: `/health` → 200; `/mcp` with no header → 401 with `{"tool": null, "status": 401, ...}` logged; `initialize` and `tools/list` with a placeholder `X-Huggingface-Token` header → 200 with `tool` correctly populated (`"initialize"`, `"tools/list"`) and all 11 tools returned; the placeholder token string does not appear anywhere in the server's stdout/stderr across the whole session (`grep -c <token> server.log` → 0).
   - Not changed: the suggestion on `client.py`'s retry/backoff deadline — it was filed as a non-blocking `suggestion`, not a must-fix, so it stays out of scope for this round per the dev-leg rework rule (only must-fix items are addressed; the main ticket doc is not touched).

Fix committed as `b8e9265` on `dev/PRD-21327` (`main` had not advanced past `a84765f` since Round 1, so no merge was needed before re-running gates).

Gates re-run on the merged-with-`main` tree, after the fix above:
- `uv run pytest -q`: **20 passed** (18 before this round; +2 new logging tests), `uv run ruff check .` and `uv run ruff format --check .` clean.
- Chinese-character scan (`grep -rnP '[\x{4e00}-\x{9fff}]' --exclude-dir=.git --exclude-dir=.venv .`): only pre-existing hits in this review file's own Round 1 finding text (literal quotations of the Chinese-language SOP §6/§14 for citation) — nothing in `src/`, `tests/`, or `README.md`.
- `gitleaks detect --source . --no-git`: no leaks found.
- README Connector section: unchanged this round (not touched).
- Local `initialize → tools/list` probe with a placeholder header: 11 tools returned, same as Round 1.

## Round 2 · 2026-11-05 · reviewed 98f7715 (base a84765f)

Verdict: approved

### Round 1 must-fix verified fixed

1. `src/huggingface_mcp/middleware.py:93-181`, `src/huggingface_mcp/__main__.py:9-14,46` — structured per-`/mcp`-request JSON logging is now present, matching the design's Non-functional wording (`docs/tickets/PRD-21327.md:167`) and the SOP §6/§14 requirement the Round 1 must-fix was filed against. Independently re-ran the gates the dev's Fixes note claims rather than taking the note on faith: `uv run pytest -q` -> 20 passed; `uv run ruff check .` and `uv run ruff format --check .` clean; `grep -rnP '[\x{4e00}-\x{9fff}]'` outside `.git` finds only this review file's own Round 1 citations of the Chinese-language SOP text, nothing in `src/`, `tests/`, or `README.md`; `gitleaks detect` clean. Read `tests/test_middleware.py::test_tool_call_emits_one_structured_log_record_without_token` and `::test_missing_header_logs_401_with_no_tool_name` directly: both assert the raw header value is absent from the serialized log record, not just that a record exists.
2. The `receive()`-replay fix (`middleware.py:111-166`) was checked against the installed MCP SDK, not just the dev's self-report: `.venv/lib/python3.12/site-packages/mcp/server/streamable_http.py::_handle_post_request` calls `request.body()` exactly once, synchronously, *before* the `anyio.create_task_group()` that starts the SSE response writer (the only place in this code path that calls `receive()` again to watch for `http.disconnect`). Because the body read happens-before the concurrent task starts, there is no race between the single replayed `http.request` message and the real `receive()` calls that follow it — the "replay once, then pass through" design is safe for the actual call order used by this SDK version, not merely for the test's happy path.
3. Confirmed `uvicorn.config.LOGGING_CONFIG` ships `"disable_existing_loggers": false` and only configures the `uvicorn` / `uvicorn.error` / `uvicorn.access` loggers — so `uvicorn.run()`'s internal `configure_logging()` call (which runs *after* `_configure_logging()` in `main()`) does not strip the plain-`%(message)s` root handler the fix relies on to keep the JSON log lines parseable.

### Findings

None. No new must-fix found in the Round 1 -> Round 2 diff (`src/huggingface_mcp/middleware.py`, `src/huggingface_mcp/__main__.py`, `tests/test_middleware.py`; scope matches the must-fix-only rework rule, no other file touched).

### Suggestions

Carried over from Round 1, still open, not blocking: `src/huggingface_mcp/client.py:16-120` retry/backoff has no overall per-call deadline (see Round 1 Suggestions #1). Correctly left untouched this round per the must-fix-only rework rule.

Status: frozen @ 98f7715
