"""get_ticket: one ticket in full, optionally with the start of its conversation."""

from typing import Any

from freshdesk_mcp.client import FreshdeskClient
from freshdesk_mcp.errors import NotFoundError
from freshdesk_mcp.models import Conversation, Ticket
from freshdesk_mcp.tools._shared import make_meta, not_found, require_ticket_id

# Freshdesk's include=conversations returns at most this many, oldest first.
INLINE_CONVERSATION_LIMIT = 10


def get_ticket(
    client: FreshdeskClient, ticket_id: int, include_conversations: bool = False
) -> dict[str, Any]:
    ticket_id = require_ticket_id(ticket_id)
    include = "requester,conversations" if include_conversations else "requester"
    try:
        response = client.get_one(f"/tickets/{ticket_id}", {"include": include})
    except NotFoundError:
        return not_found(ticket_id)

    result: dict[str, Any] = {
        "found": True,
        "ticket": Ticket.from_freshdesk(response).model_dump(mode="json"),
    }
    meta = make_meta(client)
    if include_conversations:
        raw_conversations = response.get("conversations") or []
        result["conversations"] = [
            Conversation.from_freshdesk(c).model_dump(mode="json") for c in raw_conversations
        ]
        if len(raw_conversations) >= INLINE_CONVERSATION_LIMIT:
            meta.truncated = True
            meta.notes.append(
                f"Only the first {INLINE_CONVERSATION_LIMIT} messages are shown and the thread "
                "may be longer. Use get_ticket_conversations to read all of it."
            )
    result["meta"] = meta.model_dump(mode="json")
    return result
