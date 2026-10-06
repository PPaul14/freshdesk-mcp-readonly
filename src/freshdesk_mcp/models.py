"""Agent-facing data models built from raw Freshdesk JSON.

These models decide what the agent sees. They deliberately keep only what is
useful and safe:
- plain-text fields (description_text / body_text), never HTML;
- requester id and name only, never email or phone (data minimisation);
- attachment names only, never URLs or file contents;
- long text truncated, with a flag saying so.

Every `from_freshdesk()` turns unexpected data into MalformedResponseError
instead of letting a pydantic traceback escape.
"""

from datetime import datetime
from typing import Any, Self

from pydantic import BaseModel, Field, ValidationError

from freshdesk_mcp.errors import MalformedResponseError

STATUS_NAMES = {2: "open", 3: "pending", 4: "resolved", 5: "closed"}
PRIORITY_NAMES = {1: "low", 2: "medium", 3: "high", 4: "urgent"}
# Ticket source codes from the Freshdesk API v2 docs ("Source" table).
SOURCE_NAMES = {
    1: "email",
    2: "portal",
    3: "phone",
    7: "chat",
    9: "feedback_widget",
    10: "outbound_email",
}

DESCRIPTION_MAX_CHARS = 2000
BODY_MAX_CHARS = 1500


def code_name(names: dict[int, str], code: Any) -> str:
    """Map a Freshdesk numeric code to a name; unknown codes become 'unknown:<n>'."""
    if isinstance(code, int) and not isinstance(code, bool) and code in names:
        return names[code]
    return f"unknown:{code}"


def truncate(text: str | None, limit: int) -> tuple[str, bool]:
    """Return (text cut to `limit` chars, whether it was cut)."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def attachment_names(raw: dict[str, Any]) -> list[str]:
    return [str(a["name"]) for a in raw.get("attachments") or []]


def _malformed(model: str, raw: Any, exc: Exception) -> MalformedResponseError:
    # Report only where the data was wrong (field locations), not the data itself.
    if isinstance(exc, ValidationError):
        fields = [".".join(str(p) for p in e["loc"]) for e in exc.errors()]
    else:
        fields = [type(exc).__name__]
    record_id = raw.get("id") if isinstance(raw, dict) else None
    return MalformedResponseError(
        f"Freshdesk returned {model} data in an unexpected shape.",
        details={"model": model, "id": record_id, "problems": fields[:5]},
    )


class Requester(BaseModel):
    id: int
    name: str | None = None  # None when Freshdesk didn't include requester details


class TicketSummary(BaseModel):
    """A ticket as it appears in lists and search results (no description)."""

    id: int
    subject: str
    status: str
    priority: str
    tags: list[str]
    requester_id: int
    created_at: datetime
    updated_at: datetime
    due_by: datetime | None = None

    @classmethod
    def from_freshdesk(cls, raw: dict[str, Any]) -> Self:
        try:
            return cls(
                id=raw["id"],
                subject=raw["subject"],
                status=code_name(STATUS_NAMES, raw.get("status")),
                priority=code_name(PRIORITY_NAMES, raw.get("priority")),
                tags=raw.get("tags") or [],
                requester_id=raw["requester_id"],
                created_at=raw["created_at"],
                updated_at=raw["updated_at"],
                due_by=raw.get("due_by"),
            )
        except (ValidationError, KeyError, TypeError, AttributeError) as exc:
            raise _malformed("ticket summary", raw, exc) from None


class Ticket(BaseModel):
    """One ticket in full, as returned by get_ticket."""

    id: int
    subject: str
    status: str
    priority: str
    source: str
    type: str | None = None
    tags: list[str]
    requester: Requester
    created_at: datetime
    updated_at: datetime
    due_by: datetime | None = None
    description_text: str
    description_truncated: bool
    has_attachments: bool
    attachment_names: list[str]

    @classmethod
    def from_freshdesk(cls, raw: dict[str, Any]) -> Self:
        try:
            requester_raw = raw.get("requester") or {}
            description, cut = truncate(raw.get("description_text"), DESCRIPTION_MAX_CHARS)
            names = attachment_names(raw)
            return cls(
                id=raw["id"],
                subject=raw["subject"],
                status=code_name(STATUS_NAMES, raw.get("status")),
                priority=code_name(PRIORITY_NAMES, raw.get("priority")),
                source=code_name(SOURCE_NAMES, raw.get("source")),
                type=raw.get("type"),
                tags=raw.get("tags") or [],
                # Only id and name are copied; email/phone never reach the agent.
                requester=Requester(id=raw["requester_id"], name=requester_raw.get("name")),
                created_at=raw["created_at"],
                updated_at=raw["updated_at"],
                due_by=raw.get("due_by"),
                description_text=description,
                description_truncated=cut,
                has_attachments=bool(names),
                attachment_names=names,
            )
        except (ValidationError, KeyError, TypeError, AttributeError) as exc:
            raise _malformed("ticket", raw, exc) from None


class Conversation(BaseModel):
    """One reply or note on a ticket."""

    id: int
    from_customer: bool  # Freshdesk "incoming"
    is_private_note: bool  # Freshdesk "private": internal, the customer never saw it
    author_id: int | None = None  # Freshdesk "user_id"
    created_at: datetime
    body_text: str
    body_truncated: bool
    attachment_names: list[str]

    @classmethod
    def from_freshdesk(cls, raw: dict[str, Any]) -> Self:
        try:
            body, cut = truncate(raw.get("body_text"), BODY_MAX_CHARS)
            return cls(
                id=raw["id"],
                from_customer=raw["incoming"],
                is_private_note=raw["private"],
                author_id=raw.get("user_id"),
                created_at=raw["created_at"],
                body_text=body,
                body_truncated=cut,
                attachment_names=attachment_names(raw),
            )
        except (ValidationError, KeyError, TypeError, AttributeError) as exc:
            raise _malformed("conversation", raw, exc) from None


class ResultMeta(BaseModel):
    """Paging and context information returned alongside every tool result."""

    page: int | None = None
    per_page: int | None = None
    has_more: bool = False
    truncated: bool = False
    rate_limit_remaining: int | None = None
    notes: list[str] = Field(default_factory=list)
