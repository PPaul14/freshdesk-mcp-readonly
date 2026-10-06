"""Input validation and result helpers shared by the tool functions."""

import re
from datetime import date
from typing import Any

from freshdesk_mcp.client import FreshdeskClient
from freshdesk_mcp.errors import InvalidRequestError
from freshdesk_mcp.models import PRIORITY_NAMES, STATUS_NAMES, ResultMeta

TOOL_MAX_PER_PAGE = 50

# Reverse maps: "open" -> 2, "urgent" -> 4.
STATUS_CODES = {name: code for code, name in STATUS_NAMES.items()}
PRIORITY_CODES = {name: code for code, name in PRIORITY_NAMES.items()}

_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def require_ticket_id(ticket_id: Any) -> int:
    # bool is a subclass of int in Python, so True would otherwise pass as ticket 1.
    if isinstance(ticket_id, bool) or not isinstance(ticket_id, int) or ticket_id < 1:
        raise InvalidRequestError("ticket_id must be a positive integer.")
    return ticket_id


def require_page(page: Any, max_page: int | None = None) -> int:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise InvalidRequestError("page must be a positive integer.")
    if max_page is not None and page > max_page:
        raise InvalidRequestError(f"page must be {max_page} or lower.")
    return page


def clamp_per_page(per_page: Any, notes: list[str]) -> int:
    if isinstance(per_page, bool) or not isinstance(per_page, int):
        raise InvalidRequestError("per_page must be an integer.")
    clamped = max(1, min(per_page, TOOL_MAX_PER_PAGE))
    if clamped != per_page:
        notes.append(f"per_page was adjusted to {clamped} (allowed range 1-{TOOL_MAX_PER_PAGE}).")
    return clamped


def require_choice(value: Any, allowed: dict[str, int], field: str) -> int:
    """Validate a name like 'open' against an allowed set and return its Freshdesk code."""
    if not isinstance(value, str) or value.strip().lower() not in allowed:
        raise InvalidRequestError(f"{field} must be one of: {', '.join(allowed)}.")
    return allowed[value.strip().lower()]


def require_date(value: Any, field: str) -> str:
    """Validate a YYYY-MM-DD calendar date and return it unchanged."""
    if not isinstance(value, str) or not _DATE_PATTERN.match(value):
        raise InvalidRequestError(f"{field} must be a date in YYYY-MM-DD format.")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise InvalidRequestError(f"{field} is not a real calendar date.") from None
    return value


def make_meta(client: FreshdeskClient, **fields: Any) -> ResultMeta:
    rate = client.last_rate_limit
    return ResultMeta(rate_limit_remaining=rate.remaining if rate else None, **fields)


def not_found(ticket_id: int) -> dict[str, Any]:
    return {
        "found": False,
        "ticket_id": ticket_id,
        "message": f"No ticket with id {ticket_id} was found in Freshdesk.",
    }
