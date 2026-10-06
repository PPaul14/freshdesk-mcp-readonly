"""Seed a Freshdesk trial account with fictional test tickets.

This script WRITES to Freshdesk. It lives outside the freshdesk_mcp package on
purpose: the connector is read-only and must never import this file.

Usage (from the project root):
    python scripts/seed_tickets.py --dry-run   # print the plan, no API calls
    python scripts/seed_tickets.py             # create the tickets

Re-running creates duplicate tickets; delete old ones in the Freshdesk UI first.
"""

import argparse
import html
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = Path(__file__).resolve().parent / "seed_data.json"

# Trial accounts allow 50 calls/minute. 1.5 s per call caps us at 40/minute.
SLEEP_SECONDS = 1.5
# requests waits forever by default; never let one call hang the script.
TIMEOUT_SECONDS = 30

PRIORITY_NAMES = {1: "Low", 2: "Medium", 3: "High", 4: "Urgent"}
STATUS_NAMES = {2: "Open", 3: "Pending", 4: "Resolved", 5: "Closed"}
OPEN = 2

# (payment_id, gross amount in INR) rows for the generated settlement report.
REPORT_PAYMENTS = [
    ("pay_FAKE9001", 3200.00),
    ("pay_FAKE9002", 1450.00),
    ("pay_FAKE9003", 5600.00),
    ("pay_FAKE9004", 899.00),
    ("pay_FAKE9005", 1651.00),
]
FEE_RATE = 0.02  # fictional 2% transaction fee
GST_RATE = 0.18  # GST charged on the fee


class SeedError(Exception):
    """A Freshdesk call failed; the seed run must stop."""


def to_html(text: str) -> str:
    """Freshdesk fields are HTML: escape special characters, keep line breaks."""
    return html.escape(text).replace("\n", "<br>")


def build_settlement_report() -> bytes:
    """Generate a small fictional settlement report as plain-text bytes."""
    lines = [
        "FICTIONAL SETTLEMENT REPORT - TEST DATA ONLY",
        "Settlement ID: setl_FAKE0930X",
        "Settlement date: 2026-09-30",
        "",
        "payment_id,gross_inr,fee_inr,gst_on_fee_inr,net_inr",
    ]
    totals = [0.0, 0.0, 0.0, 0.0]
    for payment_id, gross in REPORT_PAYMENTS:
        fee = round(gross * FEE_RATE, 2)
        gst = round(fee * GST_RATE, 2)
        net = round(gross - fee - gst, 2)
        for i, value in enumerate((gross, fee, gst, net)):
            totals[i] += value
        lines.append(f"{payment_id},{gross:.2f},{fee:.2f},{gst:.2f},{net:.2f}")
    lines.append("TOTAL," + ",".join(f"{t:.2f}" for t in totals))
    return ("\n".join(lines) + "\n").encode("utf-8")


class FreshdeskSeeder:
    """Minimal write-capable Freshdesk client used only by this script."""

    def __init__(self, domain: str, api_key: str) -> None:
        self.base_url = f"https://{domain}/api/v2"
        self.session = requests.Session()
        # Basic auth: API key as username, any dummy password ("X" per the docs).
        self.session.auth = (api_key, "X")

    def _call(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self.session.request(
                method, f"{self.base_url}{path}", timeout=TIMEOUT_SECONDS, **kwargs
            )
        except requests.RequestException as exc:
            raise SeedError(f"{method} {path} failed: {exc}") from exc
        time.sleep(SLEEP_SECONDS)
        if not response.ok:
            raise SeedError(f"{method} {path} -> HTTP {response.status_code}\n{response.text}")
        return response.json()

    def create_ticket(self, ticket: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "subject": ticket["subject"],
            "description": to_html(ticket["description"]),
            "name": ticket["requester"]["name"],
            "email": ticket["requester"]["email"],
            "priority": ticket["priority"],
            "status": OPEN,  # final status is applied after the conversation
            "tags": ticket["tags"],
        }
        return self._call("POST", "/tickets", json=payload)

    def add_agent_reply(self, ticket_id: int, body: str) -> None:
        self._call("POST", f"/tickets/{ticket_id}/reply", json={"body": to_html(body)})

    def add_customer_note(
        self, ticket_id: int, requester_id: int, body: str, attachment: str | None = None
    ) -> None:
        # incoming=True + the requester's user_id makes the note show as coming
        # from the customer; private=False makes it part of the public thread.
        path = f"/tickets/{ticket_id}/notes"
        if attachment is None:
            payload = {
                "body": to_html(body),
                "incoming": True,
                "private": False,
                "user_id": requester_id,
            }
            self._call("POST", path, json=payload)
            return
        # multipart/form-data: every form field is text, so booleans and ints
        # are sent as strings; the file goes in the repeated "attachments[]" field.
        form = {
            "body": to_html(body),
            "incoming": "true",
            "private": "false",
            "user_id": str(requester_id),
        }
        files = {"attachments[]": (attachment, build_settlement_report(), "text/plain")}
        self._call("POST", path, data=form, files=files)

    def set_status(self, ticket_id: int, status: int) -> None:
        self._call("PUT", f"/tickets/{ticket_id}", json={"status": status})


def describe(ticket: dict[str, Any]) -> str:
    messages = ticket["messages"]
    has_file = any("attachment" in m for m in messages)
    return (
        f"{PRIORITY_NAMES[ticket['priority']]:<6} -> {STATUS_NAMES[ticket['status']]:<8} "
        f"msgs={len(messages):<2} file={'yes' if has_file else 'no '} {ticket['subject']!r}"
    )


def calls_for(ticket: dict[str, Any]) -> int:
    """One create + one call per message + one status update."""
    return 1 + len(ticket["messages"]) + 1


def seed_ticket(seeder: FreshdeskSeeder, ticket: dict[str, Any]) -> int:
    created = seeder.create_ticket(ticket)
    ticket_id, requester_id = created["id"], created["requester_id"]
    for message in ticket["messages"]:
        if message["from"] == "agent":
            seeder.add_agent_reply(ticket_id, message["body"])
        else:
            seeder.add_customer_note(
                ticket_id, requester_id, message["body"], message.get("attachment")
            )
    seeder.set_status(ticket_id, ticket["status"])
    return ticket_id


def load_credentials() -> tuple[str, str]:
    load_dotenv(PROJECT_ROOT / ".env")
    domain = os.getenv("FRESHDESK_DOMAIN", "").strip()
    api_key = os.getenv("FRESHDESK_API_KEY", "").strip()
    if not domain or not api_key:
        sys.exit("FRESHDESK_DOMAIN and FRESHDESK_API_KEY must be set in .env")
    if "://" in domain or "/" in domain:
        sys.exit("FRESHDESK_DOMAIN must be a bare host like yourcompany.freshdesk.com")
    return domain, api_key


def main() -> int:
    parser = argparse.ArgumentParser(description="Create fictional test tickets in Freshdesk.")
    parser.add_argument(
        "--dry-run", action="store_true", help="print the plan without calling the API"
    )
    args = parser.parse_args()

    tickets: list[dict[str, Any]] = json.loads(DATA_FILE.read_text(encoding="utf-8"))["tickets"]
    total = len(tickets)

    if args.dry_run:
        for i, ticket in enumerate(tickets, start=1):
            print(f"[{i:>2}/{total}] DRY RUN  {describe(ticket)}")
        calls = sum(calls_for(t) for t in tickets)
        print(f"Would make {calls} API calls (~{calls * SLEEP_SECONDS / 60:.1f} min).")
        return 0

    domain, api_key = load_credentials()
    seeder = FreshdeskSeeder(domain, api_key)
    for i, ticket in enumerate(tickets, start=1):
        try:
            ticket_id = seed_ticket(seeder, ticket)
        except SeedError as exc:
            print(f"[{i:>2}/{total}] FAILED   {ticket['subject']!r}\n{exc}", file=sys.stderr)
            print(f"Stopped. {i - 1} ticket(s) fully created before the failure.", file=sys.stderr)
            return 1
        print(f"[{i:>2}/{total}] #{ticket_id:<6} {describe(ticket)}")
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
