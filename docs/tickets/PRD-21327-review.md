# PRD-21327 · Code review

## Round 1 · 2026-11-05 · reviewed b91af56 (base a84765f)

Verdict: changes requested

### Findings

1. [must-fix] `src/huggingface_mcp/middleware.py:47` (service-wide: no log call anywhere in `src/`) — The service has no structured request logging at all. `grep -rn "logging\|getLogger\|print(" src/` finds exactly one hit: a one-time startup banner `print()` in `src/huggingface_mcp/__main__.py:43`. There is no per-request/per-tool-call logging of tool name, status, latency, or a request-id anywhere in `middleware.py`, `client.py`, `server.py`, or any `tools/*.py` module. This contradicts the technical design's own Non-functional section (`docs/tickets/PRD-21327.md:167`: "structured request logs (tool name, status, latency, no header values)") and is a mandatory item in the vendor-mcp-development SOP: §6 "日志与可观测性" ("用结构化日志...记录: 工具名、入参摘要、API 路径与状态码、耗时、错误 code") and the §14 delivery checklist ("[ ] 结构化日志带 request-id；不打印凭据/token/敏感业务数据"). Fix: add structured logging (e.g., the stdlib `logging` module with a JSON formatter, or `structlog`) that emits one record per tool call with a request-id, tool name, outcome/status, and latency, and never logs header values or token content — matching what the design already promised.

### Suggestions

1. `src/huggingface_mcp/client.py:16-120` — The retry/backoff loop has no overall per-call deadline tracking. Each attempt can take up to `connect(5s) + read(30s) ≈ 35s`, and with `_MAX_RETRIES = 3` (4 attempts total) plus up to 3 inter-attempt sleeps, a sustained-read-timeout scenario (the Hugging Face router hanging near the full read timeout on every attempt, which is exactly the kind of upstream slowness §5 timeouts exist to bound) can push total elapsed time to roughly `4×35s + ~7s backoff ≈ 147s`, above the SOP §5 / §14 checklist requirement of "单次调用总耗时（含重试）必须 < 120s". No test exercises this path (all mocked responses in `tests/test_tools.py` return immediately, so the retry loop's sleeps are the only thing actually timed). Not flagged as a must-fix because it requires a specific, less-common failure mode (repeated hard read-timeouts rather than fast 429/503 responses, which are the documented AC8 case and stay well under budget), but worth tightening — e.g. track a monotonic `started_at` across the whole `_request` call and return `upstream_error` once the remaining budget can't fit another attempt, rather than relying solely on per-attempt timeouts.
