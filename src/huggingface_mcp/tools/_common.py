"""Shared constants for the tool modules (default/hard-cap page sizes mirror
Hugging Face's own documented API default/maximum -- docs/tickets/
PRD-21327.md Sec Decisions made by DEV01, item 4).
"""

from ..errors import error_envelope

DEFAULT_LIMIT = 20
MAX_LIMIT = 100

NO_TOKEN = error_envelope("not_configured", "No Hugging Face token available for this request")


def capped_limit(limit: int) -> int:
    return max(1, min(limit, MAX_LIMIT))
