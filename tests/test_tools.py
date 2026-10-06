"""Tool-level behaviour: normal results for 'not found'/'no matches', and input validation."""

import pytest

from freshdesk_mcp.errors import InvalidRequestError
from freshdesk_mcp.tools.get_ticket import get_ticket
from freshdesk_mcp.tools.get_ticket_conversations import get_ticket_conversations
from freshdesk_mcp.tools.list_tickets import list_tickets
from freshdesk_mcp.tools.search_tickets import build_search_query, search_tickets
from tests.conftest import BASE


def test_get_ticket_404_returns_found_false(client, rsps):
    """A missing ticket is a normal answer, not an error."""
    rsps.get(f"{BASE}/tickets/424242", status=404, json={"code": "not_found"})

    result = get_ticket(client, 424242)

    assert result["found"] is False
    assert result["ticket_id"] == 424242
    assert "424242" in result["message"]


def test_search_with_zero_results_returns_empty_list_and_note(client, rsps):
    """No matches gives tickets=[] with an explanatory note, not an error."""
    rsps.get(f"{BASE}/search/tickets", json={"total": 0, "results": []})

    result = search_tickets(client, priority=["urgent"])

    assert result["tickets"] == []
    assert any("No tickets matched" in note for note in result["meta"]["notes"])


@pytest.mark.parametrize(
    "call",
    [
        lambda c: list_tickets(c, status="escalated"),
        lambda c: search_tickets(c, status=["open", "escalated"]),
        lambda c: search_tickets(c, priority=["critical"]),
    ],
)
def test_invalid_status_or_priority_rejected_before_any_http(client, rsps, call):
    """Bad enum values are caught locally; Freshdesk is never called."""
    with pytest.raises(InvalidRequestError):
        call(client)
    assert len(rsps.calls) == 0


@pytest.mark.parametrize("tag", ["refunds' OR status:2", 'say "hi"', "", "a" * 65])
def test_unsafe_or_invalid_tag_rejected(client, rsps, tag):
    """Tags that could break out of the quoted query (or are empty/too long) are refused."""
    with pytest.raises(InvalidRequestError):
        search_tickets(client, tag=tag)
    assert len(rsps.calls) == 0


@pytest.mark.parametrize("bad_id", [True, False, 0, -5, "5", 5.0])
def test_non_positive_int_ticket_ids_rejected(client, rsps, bad_id):
    """Only real positive integers are ticket ids; True must not be read as ticket 1."""
    with pytest.raises(InvalidRequestError):
        get_ticket(client, bad_id)
    with pytest.raises(InvalidRequestError):
        get_ticket_conversations(client, bad_id)
    assert len(rsps.calls) == 0


def test_search_requires_at_least_one_filter(client, rsps):
    """An unfiltered search is refused rather than dumping every ticket."""
    with pytest.raises(InvalidRequestError):
        search_tickets(client)
    assert len(rsps.calls) == 0


def test_search_query_is_built_from_validated_values():
    """The Freshdesk query string is assembled only from checked values."""
    query = build_search_query(
        status=["pending", "open"], tag="refunds", created_after="2026-09-01"
    )
    assert query == "(status:2 OR status:3) AND tag:'refunds' AND created_at:>'2026-09-01'"


def test_impossible_date_rejected():
    """Dates must be real calendar dates, not just the right shape."""
    with pytest.raises(InvalidRequestError):
        build_search_query(created_after="2026-02-30")
