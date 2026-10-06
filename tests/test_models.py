"""Mapping raw Freshdesk JSON into the agent-facing models."""

import json

import pytest
from pydantic import ValidationError

from freshdesk_mcp.errors import MalformedResponseError
from freshdesk_mcp.models import (
    BODY_MAX_CHARS,
    DESCRIPTION_MAX_CHARS,
    PRIORITY_NAMES,
    SOURCE_NAMES,
    STATUS_NAMES,
    Conversation,
    Requester,
    Ticket,
    TicketSummary,
    code_name,
)
from tests.conftest import make_ticket


def test_known_codes_map_to_names():
    """Numeric status, priority and source codes become readable names."""
    ticket = Ticket.from_freshdesk(make_ticket(status=4, priority=4, source=1))
    assert (ticket.status, ticket.priority, ticket.source) == ("resolved", "urgent", "email")
    assert STATUS_NAMES == {2: "open", 3: "pending", 4: "resolved", 5: "closed"}
    assert PRIORITY_NAMES == {1: "low", 2: "medium", 3: "high", 4: "urgent"}
    assert SOURCE_NAMES[10] == "outbound_email"


def test_unknown_status_becomes_unknown_label_not_a_crash():
    """A code Freshdesk adds in future is reported, not fatal."""
    assert Ticket.from_freshdesk(make_ticket(status=7)).status == "unknown:7"
    assert TicketSummary.from_freshdesk(make_ticket(status=7)).status == "unknown:7"
    assert code_name(STATUS_NAMES, None) == "unknown:None"
    assert code_name(STATUS_NAMES, True) == "unknown:True"  # bool is not a real code


@pytest.mark.parametrize(
    "broken",
    [
        {k: v for k, v in make_ticket().items() if k != "subject"},  # missing field
        make_ticket(created_at="not-a-date"),  # wrong type
        make_ticket(tags="refunds"),  # string where a list is expected
    ],
)
def test_malformed_ticket_raises_malformed_response_error(broken):
    """Bad payloads raise our typed error, never a raw pydantic traceback."""
    with pytest.raises(MalformedResponseError) as info:
        Ticket.from_freshdesk(broken)
    assert not isinstance(info.value, ValidationError)
    assert info.value.details["model"] == "ticket"


def test_malformed_conversation_raises_malformed_response_error():
    """A conversation missing required fields is reported cleanly."""
    with pytest.raises(MalformedResponseError):
        Conversation.from_freshdesk({"id": 1, "body_text": "hello"})


def test_long_description_is_truncated_and_flagged():
    """Long text is cut to the limit and the agent is told it was cut."""
    long_ticket = Ticket.from_freshdesk(make_ticket(description_text="x" * 2500))
    assert len(long_ticket.description_text) == DESCRIPTION_MAX_CHARS
    assert long_ticket.description_truncated is True

    short_ticket = Ticket.from_freshdesk(make_ticket())
    assert short_ticket.description_truncated is False


def test_long_conversation_body_is_truncated_and_flagged():
    """Message bodies are cut at their own limit with a flag."""
    raw = {
        "id": 5,
        "incoming": True,
        "private": False,
        "user_id": 9001,
        "created_at": "2026-10-01T09:00:00Z",
        "body_text": "y" * 2000,
        "attachments": [{"name": "report.txt", "attachment_url": "https://files.example.com/x"}],
    }
    convo = Conversation.from_freshdesk(raw)
    assert len(convo.body_text) == BODY_MAX_CHARS
    assert convo.body_truncated is True
    assert convo.attachment_names == ["report.txt"]  # name only, never the URL


def test_requester_model_has_no_email_field():
    """Requester emails can never reach the agent: the model has nowhere to put them."""
    assert set(Requester.model_fields) == {"id", "name"}
    dumped = json.dumps(Ticket.from_freshdesk(make_ticket()).model_dump(mode="json"))
    assert "example.com" not in dumped
    assert "email" not in dumped
