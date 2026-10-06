"""Link-header pagination, the MAX_PAGES ceiling and per_page clamping."""

from responses import matchers

from freshdesk_mcp.client import MAX_PAGES, MAX_PER_PAGE
from tests.conftest import BASE

URL = f"{BASE}/tickets"


def _next_link(page: int) -> dict[str, str]:
    return {"Link": f'<{URL}?page={page}&per_page={MAX_PER_PAGE}>; rel="next"'}


def _page(page: int) -> list:
    return [matchers.query_param_matcher({"page": str(page), "per_page": str(MAX_PER_PAGE)})]


def test_three_pages_are_collected_by_following_next_links(client, rsps):
    """get_pages follows rel="next" and stops when it disappears."""
    rsps.get(URL, json=[{"id": 1}], headers=_next_link(2), match=_page(1))
    rsps.get(URL, json=[{"id": 2}], headers=_next_link(3), match=_page(2))
    rsps.get(URL, json=[{"id": 3}], match=_page(3))  # no Link header: last page

    result = client.get_pages("/tickets")

    assert [t["id"] for t in result.items] == [1, 2, 3]
    assert result.truncated is False
    assert len(rsps.calls) == 3


def test_endless_next_links_stop_at_max_pages_and_report_truncation(client, rsps):
    """A runaway Link chain can never fetch more than MAX_PAGES pages."""
    rsps.get(URL, json=[{"id": 1}], headers=_next_link(99))  # every page claims there's more

    result = client.get_pages("/tickets", max_pages=1000)

    assert len(rsps.calls) == MAX_PAGES
    assert len(result.items) == MAX_PAGES
    assert result.truncated is True


def test_per_page_is_clamped_to_freshdesk_limits(client, rsps):
    """per_page above 100 or below 1 is clamped before the request is sent."""
    rsps.get(URL, json=[], match=[matchers.query_param_matcher({"page": "1", "per_page": "100"})])
    rsps.get(URL, json=[], match=[matchers.query_param_matcher({"page": "1", "per_page": "1"})])

    assert client.get_page("/tickets", per_page=500).items == []
    assert client.get_page("/tickets", per_page=0).items == []


def test_empty_page_stops_even_if_a_next_link_is_present(client, rsps):
    """An empty page ends pagination instead of looping on blank pages."""
    rsps.get(URL, json=[{"id": 1}], headers=_next_link(2), match=_page(1))
    rsps.get(URL, json=[], headers=_next_link(3), match=_page(2))

    result = client.get_pages("/tickets")

    assert [t["id"] for t in result.items] == [1]
    assert result.truncated is False
    assert len(rsps.calls) == 2
