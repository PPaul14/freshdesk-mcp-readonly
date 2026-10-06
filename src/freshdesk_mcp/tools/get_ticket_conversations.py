"""get_ticket_conversations: read a ticket's full thread, page by page, oldest first.

Private notes (internal agent notes the customer never saw) are included but
flagged with is_private_note.
"""

from typing import Any

from freshdesk_mcp.client import FreshdeskClient
from freshdesk_mcp.errors import NotFoundError
from freshdesk_mcp.models import BODY_MAX_CHARS, Conversation
from freshdesk_mcp.tools._shared import (
    clamp_per_page,
    make_meta,
    not_found,
    require_page,
    require_ticket_id,
)


def get_ticket_conversations(
    client: FreshdeskClient, ticket_id: int, page: int = 1, per_page: int = 20
) -> dict[str, Any]:
    ticket_id = require_ticket_id(ticket_id)
    page = require_page(page)
    notes: list[str] = []
    per_page = clamp_per_page(per_page, notes)
    try:
        # Freshdesk returns conversations oldest first by default.
        result = client.get_page(
            f"/tickets/{ticket_id}/conversations", page=page, per_page=per_page
        )
    except NotFoundError:
        return {**not_found(ticket_id), "conversations": []}

    conversations = [Conversation.from_freshdesk(c) for c in result.items]
    if not conversations:
        notes.append("No messages on this page.")
    if any(c.body_truncated for c in conversations):
        notes.append(f"Some message bodies were cut at {BODY_MAX_CHARS} characters.")
    if result.has_more:
        notes.append(f"Thread continues; call again with page={page + 1}.")
    meta = make_meta(client, page=page, per_page=per_page, has_more=result.has_more, notes=notes)
    return {
        "found": True,
        "ticket_id": ticket_id,
        "conversations": [c.model_dump(mode="json") for c in conversations],
        "meta": meta.model_dump(mode="json"),
    }
