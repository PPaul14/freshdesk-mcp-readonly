"""Shared test fixtures. All data is fictional; no test touches the network."""

from collections.abc import Iterator

import pytest
import responses

from freshdesk_mcp.client import FreshdeskClient

DOMAIN = "fictional-shop.freshdesk.com"
BASE = f"https://{DOMAIN}/api/v2"
FAKE_KEY = "FAKE-TEST-KEY-123"
JITTER_FRACTION = 0.5  # fixed random_fn value -> jitter of 0.5 * 0.5 s = 0.25 s


@pytest.fixture
def sleeps() -> list[float]:
    """Every delay the client asked for, in order, instead of really sleeping."""
    return []


@pytest.fixture
def client(sleeps: list[float]) -> FreshdeskClient:
    return FreshdeskClient(
        DOMAIN, FAKE_KEY, sleep_fn=sleeps.append, random_fn=lambda: JITTER_FRACTION
    )


@pytest.fixture
def rsps() -> Iterator[responses.RequestsMock]:
    """Mocked HTTP. Any request without a registered response fails the test."""
    with responses.RequestsMock() as mock:
        yield mock


def make_ticket(**overrides: object) -> dict[str, object]:
    """A raw Freshdesk ticket payload for a fictional requester."""
    ticket: dict[str, object] = {
        "id": 101,
        "subject": "Refund not received for order FX-0001",
        "status": 2,
        "priority": 3,
        "source": 2,
        "type": None,
        "tags": ["refunds"],
        "requester_id": 9001,
        "requester": {"id": 9001, "name": "Test Persona", "email": "test.persona@example.com"},
        "created_at": "2026-10-01T09:00:00Z",
        "updated_at": "2026-10-02T09:00:00Z",
        "due_by": "2026-10-03T09:00:00Z",
        "description_text": "Customer says the refund has not arrived.",
        "attachments": [],
    }
    ticket.update(overrides)
    return ticket
