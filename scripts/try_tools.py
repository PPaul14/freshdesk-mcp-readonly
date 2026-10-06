"""Call each tool once against the real Freshdesk in .env and print the JSON results.

Usage (from the project root):
    python scripts/try_tools.py

Read-only. Never prints the domain or the API key.
"""

import json
import sys
from collections.abc import Callable
from typing import Any

from freshdesk_mcp.client import FreshdeskClient
from freshdesk_mcp.errors import FreshdeskError
from freshdesk_mcp.tools.get_ticket import get_ticket
from freshdesk_mcp.tools.get_ticket_conversations import get_ticket_conversations
from freshdesk_mcp.tools.list_tickets import list_tickets
from freshdesk_mcp.tools.search_tickets import search_tickets


def show(title: str, call: Callable[[], dict[str, Any]]) -> None:
    print(f"\n===== {title} =====")
    try:
        print(json.dumps(call(), indent=2))
    except FreshdeskError as exc:
        print(json.dumps({"error": exc.to_dict()}, indent=2))


def main() -> int:
    try:
        client = FreshdeskClient.from_env()
    except FreshdeskError as exc:
        print(f"FAILED [{exc.code}]: {exc.message}", file=sys.stderr)
        return 1
    print("client: OK")

    show("list_tickets(status='open')", lambda: list_tickets(client, status="open"))
    show(
        "get_ticket(5, include_conversations=True)",
        lambda: get_ticket(client, 5, include_conversations=True),
    )
    show("search_tickets(priority=['urgent'])", lambda: search_tickets(client, priority=["urgent"]))
    show("get_ticket_conversations(5, page=1)", lambda: get_ticket_conversations(client, 5, page=1))
    show("get_ticket(999999)", lambda: get_ticket(client, 999999))
    return 0


if __name__ == "__main__":
    sys.exit(main())
