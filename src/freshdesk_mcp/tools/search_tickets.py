"""search_tickets: find tickets by structured filters.

CAN match:
- status (any of: open, pending, resolved, closed)
- priority (any of: low, medium, high, urgent)
- one tag (exact tag match, e.g. "refunds")
- created_at / updated_at date ranges (YYYY-MM-DD, both ends inclusive)
All given filters are combined with AND; several statuses or priorities are OR-ed.

CANNOT match:
- free text: words in the subject, description or conversation bodies
- requester name or email, agent, group, company, or custom fields

Freshdesk returns 30 results per page and at most 10 pages (300 tickets) for a
search. We build the Freshdesk query string ourselves from validated values, so
no caller-supplied text is ever pasted into it unchecked.
"""

import re
from typing import Any

from freshdesk_mcp.client import SEARCH_MAX_PAGE, SEARCH_PAGE_SIZE, FreshdeskClient
from freshdesk_mcp.errors import InvalidRequestError
from freshdesk_mcp.models import TicketSummary
from freshdesk_mcp.tools._shared import (
    PRIORITY_CODES,
    STATUS_CODES,
    make_meta,
    require_choice,
    require_date,
    require_page,
)

# Tags are pasted inside '...' in the query, so quotes and other syntax are not allowed.
_TAG_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}$")


def _any_of(field: str, codes: list[int]) -> str:
    clause = " OR ".join(f"{field}:{code}" for code in codes)
    return f"({clause})" if len(codes) > 1 else clause


def build_search_query(
    status: list[str] | None = None,
    priority: list[str] | None = None,
    tag: str | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
    updated_after: str | None = None,
) -> str:
    """Validate every filter and return the bare Freshdesk query (without outer quotes)."""
    # Accept a single string too ("open"), otherwise iterating it would yield letters.
    if isinstance(status, str):
        status = [status]
    if isinstance(priority, str):
        priority = [priority]
    clauses: list[str] = []
    if status:
        codes = sorted({require_choice(s, STATUS_CODES, "status") for s in status})
        clauses.append(_any_of("status", codes))
    if priority:
        codes = sorted({require_choice(p, PRIORITY_CODES, "priority") for p in priority})
        clauses.append(_any_of("priority", codes))
    if tag is not None:
        if not isinstance(tag, str) or not _TAG_PATTERN.match(tag.strip()):
            raise InvalidRequestError(
                "tag may contain only letters, digits, spaces, '_', '.', '-' (max 64 chars)."
            )
        clauses.append(f"tag:'{tag.strip()}'")
    if created_after is not None:
        clauses.append(f"created_at:>'{require_date(created_after, 'created_after')}'")
    if created_before is not None:
        clauses.append(f"created_at:<'{require_date(created_before, 'created_before')}'")
    if created_after and created_before and created_after > created_before:
        raise InvalidRequestError("created_after must not be later than created_before.")
    if updated_after is not None:
        clauses.append(f"updated_at:>'{require_date(updated_after, 'updated_after')}'")
    if not clauses:
        raise InvalidRequestError("Give at least one filter: status, priority, tag or a date.")
    return " AND ".join(clauses)


def run_search(client: FreshdeskClient, query: str, page: int) -> dict[str, Any]:
    """Run a built query and shape the result. Shared with list_tickets."""
    result = client.search_page(query, page)
    tickets = [TicketSummary.from_freshdesk(r).model_dump(mode="json") for r in result.results]
    reachable = SEARCH_PAGE_SIZE * SEARCH_MAX_PAGE
    meta = make_meta(
        client,
        page=page,
        per_page=SEARCH_PAGE_SIZE,
        has_more=result.has_more,
        truncated=result.total > reachable,
    )
    meta.notes.append(f"Freshdesk reports {result.total} matching ticket(s) in total.")
    if not tickets:
        meta.notes.append("No tickets matched these filters on this page.")
    if result.has_more:
        meta.notes.append(f"More results exist; call again with page={page + 1}.")
    if result.total > reachable:
        meta.notes.append(
            f"Search can only reach the first {reachable} results; narrow the filters to see "
            "the rest."
        )
    return {"tickets": tickets, "meta": meta.model_dump(mode="json")}


def search_tickets(
    client: FreshdeskClient,
    status: list[str] | None = None,
    priority: list[str] | None = None,
    tag: str | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
    updated_after: str | None = None,
    page: int = 1,
) -> dict[str, Any]:
    query = build_search_query(status, priority, tag, created_after, created_before, updated_after)
    return run_search(client, query, require_page(page, SEARCH_MAX_PAGE))
