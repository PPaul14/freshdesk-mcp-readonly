"""list_tickets: browse recent tickets, optionally by status or priority.

Without status/priority this reads GET /tickets (newest first). Freshdesk's list
endpoint cannot filter by status or priority, and filtering one page in Python
would return misleading empty pages, so with those filters we use the search
endpoint instead (30 results per page, at most 10 pages).
"""

from typing import Any

from freshdesk_mcp.client import SEARCH_MAX_PAGE, FreshdeskClient
from freshdesk_mcp.models import TicketSummary
from freshdesk_mcp.tools._shared import (
    PRIORITY_CODES,
    STATUS_CODES,
    clamp_per_page,
    make_meta,
    require_choice,
    require_date,
    require_page,
)
from freshdesk_mcp.tools.search_tickets import build_search_query, run_search


def list_tickets(
    client: FreshdeskClient,
    status: str | None = None,
    priority: str | None = None,
    updated_since: str | None = None,
    page: int = 1,
    per_page: int = 20,
) -> dict[str, Any]:
    notes: list[str] = []
    per_page = clamp_per_page(per_page, notes)
    if updated_since is not None:
        require_date(updated_since, "updated_since")

    if status is not None or priority is not None:
        if status is not None:
            require_choice(status, STATUS_CODES, "status")
        if priority is not None:
            require_choice(priority, PRIORITY_CODES, "priority")
        query = build_search_query(
            status=[status] if status else None,
            priority=[priority] if priority else None,
            updated_after=updated_since,
        )
        result = run_search(client, query, require_page(page, SEARCH_MAX_PAGE))
        result["meta"]["notes"] = (
            notes
            + ["Filtered by status/priority via search: 30 results per page; per_page is ignored."]
            + result["meta"]["notes"]
        )
        return result

    page = require_page(page)
    params: dict[str, Any] = {}
    if updated_since is not None:
        params["updated_since"] = f"{updated_since}T00:00:00Z"
    else:
        notes.append(
            "Only tickets created in the last 30 days are included (Freshdesk default). "
            "Pass updated_since=YYYY-MM-DD to include older tickets."
        )
    result = client.get_page("/tickets", params, page=page, per_page=per_page)
    tickets = [TicketSummary.from_freshdesk(r).model_dump(mode="json") for r in result.items]
    if not tickets:
        notes.append("No tickets on this page.")
    if result.has_more:
        notes.append(f"More tickets exist; call again with page={page + 1}.")
    meta = make_meta(client, page=page, per_page=per_page, has_more=result.has_more, notes=notes)
    return {"tickets": tickets, "meta": meta.model_dump(mode="json")}
