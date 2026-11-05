"""Structured error envelope, status-code classification, and token-economical
JSON serialization (vendor-mcp-development SOP §2.5 / §4).

Tools never raise across the tool boundary; they catch HuggingFaceError
(client.py) and return `error_envelope(...)` (or `.to_envelope()`) as the
tool result string, so an Agent can branch on `code` and decide whether to
retry.
"""

import json
from typing import Any, Literal

ErrorCode = Literal[
    "not_configured",
    "unauthorized",
    "not_found",
    "invalid_argument",
    "rate_limited",
    "upstream_error",
]

# retryable=True only for the two codes that represent a transient upstream
# condition; everything else is a caller-side or permanent condition.
_RETRYABLE: dict[str, bool] = {
    "not_configured": False,
    "unauthorized": False,
    "not_found": False,
    "invalid_argument": False,
    "rate_limited": True,
    "upstream_error": True,
}

# HTTP status -> error code (SOP §4.2's fixed vocabulary). Status 0 means a
# network/connection-level failure (no response at all) and is handled
# separately in classify_status, not in this table.
_STATUS_TO_CODE: dict[int, ErrorCode] = {
    400: "invalid_argument",
    401: "unauthorized",
    403: "unauthorized",
    404: "not_found",
    422: "invalid_argument",
    429: "rate_limited",
}

MAX_RESULT_CHARS = 20_000


def classify_status(status_code: int) -> ErrorCode:
    """Map an HTTP status code to the fixed error vocabulary.

    0 (no response received) and any 5xx map to upstream_error/retryable;
    unrecognized 4xx map to invalid_argument -- there is no "unknown" code
    in the fixed vocabulary (SOP §4.2).
    """
    if status_code in _STATUS_TO_CODE:
        return _STATUS_TO_CODE[status_code]
    if status_code == 0 or status_code >= 500:
        return "upstream_error"
    return "invalid_argument"


def error_envelope(code: ErrorCode, message: str, retryable: bool | None = None) -> str:
    """Serialize the SOP §4.2 error envelope as compact JSON.

    `retryable` is derived from `code` when omitted; pass it explicitly only
    to override (e.g. folding in `Retry-After`-driven uncertainty).
    """
    payload = {
        "error": {
            "code": code,
            "message": message,
            "retryable": _RETRYABLE.get(code, False) if retryable is None else retryable,
        }
    }
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def dump_json(data: Any, max_chars: int = MAX_RESULT_CHARS) -> str:
    """Serialize compactly (SOP §2.5: no indent, ensure_ascii=False).

    Truncates the largest list field (or a top-level list) when the result
    would exceed max_chars, rather than ever returning an unbounded blob.
    Truncated output carries `truncated`/`original_count` so the caller
    knows the result was cut rather than genuinely small.
    """
    s = _compact(data)
    if len(s) <= max_chars:
        return s

    if isinstance(data, list):
        return _truncate_list(data, max_chars, wrap_key="items")

    if isinstance(data, dict):
        list_keys = [k for k, v in data.items() if isinstance(v, list)]
        if list_keys:
            key = max(list_keys, key=lambda k: len(_compact(data[k])))
            items = data[key]
            lo, hi = 0, len(items)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                candidate = dict(data)
                candidate[key] = items[:mid]
                candidate["truncated"] = True
                candidate["truncated_field"] = key
                candidate["original_count"] = len(items)
                if len(_compact(candidate)) <= max_chars:
                    lo = mid
                else:
                    hi = mid - 1
            candidate = dict(data)
            candidate[key] = items[:lo]
            candidate["truncated"] = True
            candidate["truncated_field"] = key
            candidate["original_count"] = len(items)
            return _compact(candidate)

    # Not list-shaped and still too big -- return a short notice instead of
    # a giant unbounded string.
    return _compact(
        {
            "truncated": True,
            "note": f"Result too large ({len(s)} chars) to return in full and not "
            "list-shaped; narrow your query.",
        }
    )


def _compact(data: Any) -> str:
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False)


def _truncate_list(items: list, max_chars: int, wrap_key: str) -> str:
    lo, hi = 0, len(items)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        candidate = {wrap_key: items[:mid], "truncated": True, "original_count": len(items)}
        if len(_compact(candidate)) <= max_chars:
            lo = mid
        else:
            hi = mid - 1
    return _compact({wrap_key: items[:lo], "truncated": True, "original_count": len(items)})
