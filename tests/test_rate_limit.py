"""Retry-After handling and rate-limit header parsing (real Freshdesk "50.0" format)."""

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from freshdesk_mcp import server
from freshdesk_mcp.client import LOW_REMAINING_PAUSE_SECONDS, RateLimitInfo, _int_header
from freshdesk_mcp.errors import RateLimitedError
from tests.conftest import BASE

URL = f"{BASE}/tickets/1"


def test_429_with_retry_after_waits_exactly_that_long(client, rsps, sleeps):
    """Retry-After is honoured exactly, with no backoff or jitter added."""
    rsps.get(URL, status=429, headers={"Retry-After": "2"})
    rsps.get(URL, json={"id": 1})

    assert client.get_one("/tickets/1") == {"id": 1}
    assert sleeps == [2.0]


def test_retry_after_above_budget_raises_immediately_without_sleeping(client, rsps, sleeps):
    """A wait longer than the time budget fails fast and tells the caller how long to wait."""
    rsps.get(URL, status=429, headers={"Retry-After": "60"})

    with pytest.raises(RateLimitedError) as info:
        client.get_one("/tickets/1")

    assert sleeps == []
    assert len(rsps.calls) == 1
    assert info.value.retry_after_seconds == 60.0
    assert info.value.to_dict()["details"]["retry_after_seconds"] == 60.0


def test_decimal_headers_are_parsed_and_trigger_low_remaining_pause(client, rsps, sleeps):
    """Freshdesk's "50.0"/"3.0" headers are read, and low remaining pauses the next call."""
    headers = {"X-RateLimit-Total": "50.0", "X-RateLimit-Remaining": "3.0"}
    rsps.get(URL, json={"id": 1}, headers=headers)
    rsps.get(URL, json={"id": 1}, headers=headers)

    client.get_one("/tickets/1")
    assert client.last_rate_limit == RateLimitInfo(total=50, remaining=3)
    assert sleeps == []  # no pause before the first call: nothing known yet

    client.get_one("/tickets/1")
    assert sleeps == [LOW_REMAINING_PAUSE_SECONDS]


def test_healthy_remaining_does_not_pause(client, rsps, sleeps):
    """Plenty of calls left means no artificial delay."""
    headers = {"X-RateLimit-Total": "50.0", "X-RateLimit-Remaining": "40.0"}
    rsps.get(URL, json={"id": 1}, headers=headers)

    client.get_one("/tickets/1")
    client.get_one("/tickets/1")
    assert sleeps == []


def test_junk_rate_limit_headers_are_ignored_not_crashing(client, rsps):
    """Unparseable header values become None instead of breaking the request."""
    rsps.get(
        URL, json={"id": 1}, headers={"X-RateLimit-Total": "inf", "X-RateLimit-Remaining": "abc"}
    )

    assert client.get_one("/tickets/1") == {"id": 1}
    assert client.last_rate_limit is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [("50", 50), ("50.0", 50), ("3.0", 3), ("inf", None), ("nan", None), ("abc", None)],
)
def test_int_header_parsing(value, expected):
    """Header parsing accepts integers and decimals and rejects junk safely."""
    assert _int_header({"X": value}, "X") == expected


@pytest.mark.parametrize(
    ("retry_after", "expected"),
    [
        (40.0, "Freshdesk rate limit reached; retry after 40 seconds."),
        (40.2, "Freshdesk rate limit reached; retry after 41 seconds."),
        (1.0, "Freshdesk rate limit reached; retry after 1 second."),
        (None, RateLimitedError.DEFAULT_MESSAGE),
    ],
)
def test_rate_limited_message_states_the_wait(retry_after, expected):
    """The wait is in the message (rounded up, so the agent never retries too early)."""
    assert RateLimitedError(retry_after_seconds=retry_after).message == expected


def test_agent_sees_retry_wait_in_tool_error(client, rsps, sleeps, monkeypatch):
    """Via the real MCP tool wrapper, a 429 too long to wait out tells the agent how long."""
    monkeypatch.setattr(server, "_client", client)
    rsps.get(URL, status=429, headers={"Retry-After": "40"})  # 40 s > 30 s budget: no retry

    with pytest.raises(ToolError) as info:
        server.get_ticket(ticket_id=1)

    assert str(info.value) == "[rate_limited] Freshdesk rate limit reached; retry after 40 seconds."
    assert sleeps == []
