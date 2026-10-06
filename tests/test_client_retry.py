"""Retries, error mapping and the GET-only guarantee of FreshdeskClient."""

import inspect
import json

import pytest
import requests
import responses

from freshdesk_mcp.client import FreshdeskClient
from freshdesk_mcp.errors import AuthError, NetworkError, NotFoundError, UpstreamError
from tests.conftest import BASE, FAKE_KEY

URL = f"{BASE}/tickets/1"


def test_503_twice_then_200_succeeds_with_growing_waits(client, rsps, sleeps):
    """Transient 5xx errors are retried with exponential backoff plus jitter."""
    rsps.get(URL, status=503)
    rsps.get(URL, status=503)
    rsps.get(URL, json={"id": 1})

    assert client.get_one("/tickets/1") == {"id": 1}
    assert len(rsps.calls) == 3
    assert sleeps == [1.25, 2.25]  # 1 s then 2 s backoff, each + 0.25 s fixed jitter


def test_503_forever_raises_upstream_error_after_max_retries(client, rsps, sleeps):
    """A permanently failing server gives up after max_retries + 1 attempts."""
    rsps.get(URL, status=503)  # responses reuses the last match for every call

    with pytest.raises(UpstreamError) as info:
        client.get_one("/tickets/1")

    assert len(rsps.calls) == client.max_retries + 1
    assert len(sleeps) == client.max_retries
    assert info.value.details["status"] == 503


def test_404_raises_not_found_without_retry(client, rsps, sleeps):
    """A missing resource is never retried."""
    rsps.get(URL, status=404, json={"code": "not_found"})

    with pytest.raises(NotFoundError):
        client.get_one("/tickets/1")

    assert len(rsps.calls) == 1
    assert sleeps == []


def test_401_raises_auth_error_once_and_never_leaks_key(client, rsps, sleeps):
    """Bad credentials fail fast and the API key never appears in the error."""
    rsps.get(URL, status=401, json={"code": "invalid_credentials", "message": "Bad key"})

    with pytest.raises(AuthError) as info:
        client.get_one("/tickets/1")

    assert len(rsps.calls) == 1
    assert sleeps == []
    assert "Admin > Agents" in info.value.message
    for text in (str(info.value), info.value.message, json.dumps(info.value.to_dict())):
        assert FAKE_KEY not in text
    assert FAKE_KEY not in repr(client)


def test_timeout_is_retried_then_becomes_network_error(client, rsps, sleeps):
    """Timeouts are retried and surface as NetworkError, never a raw requests exception."""
    rsps.get(URL, body=requests.exceptions.ConnectTimeout(f"timed out {URL}"))

    with pytest.raises(NetworkError) as info:
        client.get_one("/tickets/1")

    assert len(rsps.calls) == client.max_retries + 1
    assert not isinstance(info.value, requests.RequestException)
    # The original exception (which holds the URL and auth header) is dropped from the chain.
    assert info.value.__cause__ is None
    assert info.value.__suppress_context__ is True


def test_client_is_structurally_get_only():
    """The client has no way to send anything but GET."""
    params = inspect.signature(FreshdeskClient._request).parameters
    assert "method" not in params
    for name in ("post", "put", "patch", "delete"):
        assert not hasattr(FreshdeskClient, name)


def test_every_request_is_a_get(client, rsps):
    """Requests that do go out use the GET method."""
    rsps.get(URL, json={"id": 1})
    client.get_one("/tickets/1")
    assert [call.request.method for call in rsps.calls] == [responses.GET]
