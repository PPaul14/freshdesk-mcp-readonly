"""Manual smoke test: talk to the real Freshdesk account in .env with GET requests only.

Usage (from the project root):
    python scripts/smoke_test.py

Prints counts, IDs, subjects and rate-limit numbers. Never prints the API key.
"""

import sys

from freshdesk_mcp.client import FreshdeskClient
from freshdesk_mcp.errors import AuthError, FreshdeskError


def main() -> int:
    try:
        client = FreshdeskClient.from_env()
        print("client: OK")

        page = client.get_page("/tickets", page=1, per_page=5)
        print(f"Tickets on page 1: {len(page.items)}")
        if page.items:
            first = page.items[0]
            print(f"First ticket: #{first.get('id')} {first.get('subject')!r}")
        print(f"has_more: {page.has_more}")
        rate = client.last_rate_limit
        if rate:
            print(f"Rate limit: total={rate.total} remaining={rate.remaining}")
        else:
            print("Rate limit: headers not present")

        search = client.search_page("status:2")
        print(f"Search 'status:2' (Open) total: {search.total}")
    except FreshdeskError as exc:
        print(f"FAILED [{exc.code}]: {exc.message}", file=sys.stderr)
        return 1

    # Same domain, deliberately wrong key: should raise a clean AuthError.
    bad_client = FreshdeskClient(client.domain, "wrong-key", max_retries=0)
    try:
        bad_client.get_page("/tickets", per_page=1)
    except AuthError as exc:
        print(f"Wrong key -> AuthError [{exc.code}]: {exc.message}")
    except FreshdeskError as exc:
        print(f"Wrong key -> unexpected {type(exc).__name__} [{exc.code}]: {exc.message}")
        return 1
    else:
        print("Wrong key -> no error raised (unexpected)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
