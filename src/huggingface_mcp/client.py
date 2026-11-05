"""Async httpx wrapper for the Hugging Face Hub REST API and the Inference
Providers router (vendor-mcp-development SOP §5: outbound timeouts, bounded
retry with backoff honoring Retry-After, one client per call).
"""

import asyncio
from typing import Any

import httpx

from .errors import classify_status, error_envelope

HUB_BASE_URL = "https://huggingface.co"
ROUTER_BASE_URL = "https://router.huggingface.co/v1"

_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3
_MAX_BACKOFF_SECONDS = 20.0


class HuggingFaceError(Exception):
    """Raised for any non-2xx Hub/router response or network failure.

    status_code 0 means no response was ever received (connection error,
    exhausted retries on a network failure).
    """

    def __init__(self, status_code: int, message: str, estimated_time: float | None = None):
        self.status_code = status_code
        self.message = message
        self.estimated_time = estimated_time
        super().__init__(f"Hugging Face API error {status_code}: {message}")

    def to_envelope(self) -> str:
        code = classify_status(self.status_code)
        message = self.message
        if self.estimated_time is not None:
            message = f"{message} (estimated_time={self.estimated_time}s)"
        return error_envelope(code, message)


class HuggingFaceClient:
    """Thin async httpx wrapper, constructed fresh per tool call from that
    request's own X-Huggingface-Token (SOP §3: no module-level singleton
    ever holds a credential). A new httpx.AsyncClient backs each outbound
    call too, so no connection pool is shared across tenants either --
    per the technical design's explicit choice (docs/tickets/PRD-21327.md
    § Change list, client.py).
    """

    def __init__(self, token: str):
        self._token = token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    async def hub_get(self, path: str, params: dict | None = None) -> Any:
        return await self._request("GET", HUB_BASE_URL, path, params=params)

    async def hub_post(self, path: str, json_body: Any = None) -> Any:
        return await self._request("POST", HUB_BASE_URL, path, json_body=json_body)

    async def router_post(self, path: str, json_body: Any) -> Any:
        return await self._request("POST", ROUTER_BASE_URL, path, json_body=json_body)

    async def _request(
        self,
        method: str,
        base_url: str,
        path: str,
        params: dict | None = None,
        json_body: Any = None,
    ) -> Any:
        url = f"{base_url}{path}"
        clean_params = {k: v for k, v in (params or {}).items() if v is not None} or None
        last_exc: Exception | None = None

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            for attempt in range(_MAX_RETRIES + 1):
                try:
                    resp = await client.request(
                        method,
                        url,
                        headers=self._headers(),
                        params=clean_params,
                        json=json_body,
                    )
                except httpx.RequestError as exc:
                    last_exc = exc
                    if attempt < _MAX_RETRIES:
                        await asyncio.sleep(min(2**attempt, _MAX_BACKOFF_SECONDS))
                        continue
                    raise HuggingFaceError(0, f"{exc or type(exc).__name__} (url={url})") from exc

                if resp.status_code in _RETRYABLE_STATUS and attempt < _MAX_RETRIES:
                    await asyncio.sleep(self._retry_delay(resp, attempt))
                    continue

                if resp.status_code >= 400:
                    raise self._to_error(resp)
                if resp.status_code == 204 or not resp.content:
                    return None
                try:
                    return resp.json()
                except ValueError:
                    return {"raw_response": resp.text}

        if last_exc:
            raise HuggingFaceError(0, str(last_exc)) from last_exc
        raise HuggingFaceError(0, "request failed with no response")

    def _retry_delay(self, resp: httpx.Response, attempt: int) -> float:
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), _MAX_BACKOFF_SECONDS)
            except ValueError:
                pass
        return min(2**attempt, _MAX_BACKOFF_SECONDS)

    def _to_error(self, resp: httpx.Response) -> HuggingFaceError:
        try:
            detail = resp.json()
        except ValueError:
            detail = resp.text
        message: Any = detail
        estimated_time = None
        if isinstance(detail, dict):
            message = detail.get("error") or detail.get("message") or str(detail)
            estimated_time = detail.get("estimated_time")
        return HuggingFaceError(resp.status_code, str(message), estimated_time=estimated_time)
