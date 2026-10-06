"""Generate the README images in docs/images/ from real command and tool output.

Usage (from the project root, with .env configured; read-only against Freshdesk):
    python scripts/make_readme_images.py

Every image is rendered in memory and checked for the Freshdesk subdomain, the API key,
"@", the local user's name and user-folder paths. If any image fails, nothing is written.
"""

import io
import json
import subprocess
import sys
from pathlib import Path

import responses
from dotenv import dotenv_values
from mcp.server.mcpserver.exceptions import ToolError
from rich.align import Align
from rich.console import Console, Group
from rich.json import JSON
from rich.panel import Panel
from rich.text import Text

from freshdesk_mcp import server
from freshdesk_mcp.client import FreshdeskClient
from freshdesk_mcp.tools.get_ticket import get_ticket
from freshdesk_mcp.tools.search_tickets import search_tickets

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "docs" / "images"
WIDTH = 96


def new_console() -> Console:
    # Render into memory only; nothing is printed to the terminal.
    return Console(
        record=True, width=WIDTH, file=io.StringIO(), force_terminal=True, color_system="truecolor"
    )


def prompt(console: Console, command: str) -> None:
    console.print(Text(command, style="bold cyan"))


def forbidden_strings() -> list[str]:
    env = dotenv_values(ROOT / ".env")
    needles = ["@", "Users\\", "Users/"]
    # The local user's name, taken from the home folder so it is never written into this file.
    needles += [part for part in Path.home().name.split() if len(part) >= 3]
    domain = (env.get("FRESHDESK_DOMAIN") or "").strip()
    key = (env.get("FRESHDESK_API_KEY") or "").strip()
    if domain:
        needles.append(domain.split(".")[0])
    if key:
        needles.append(key)
    return needles


def render(console: Console, name: str, title: str) -> tuple[str, str, str]:
    """Return (file name, visible text, svg) for one recorded console."""
    text = console.export_text(clear=False)
    svg = console.export_svg(title=title, unique_id=Path(name).stem.replace("-", "_"))
    return name, text, svg


def tests_image() -> tuple[str, str, str]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--color=yes", "-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        sys.exit("pytest failed; fix the tests before generating images.")
    console = new_console()
    prompt(console, "$ pytest -q")
    console.print(Text.from_ansi(result.stdout.rstrip()))
    return render(console, "tests.svg", "Test suite")


def tool_call_image(client: FreshdeskClient) -> tuple[str, str, str]:
    result = search_tickets(client, priority=["urgent"], status=["open"])
    result["tickets"] = result["tickets"][:3]
    console = new_console()
    prompt(console, '>>> search_tickets(priority=["urgent"], status=["open"])')
    console.print(Text("# live Freshdesk result, first 3 tickets shown", style="dim"))
    console.print(JSON(json.dumps(result)))
    return render(console, "tool-call.svg", "Tool call: search_tickets")


def not_found_image(client: FreshdeskClient) -> tuple[str, str, str]:
    console = new_console()
    prompt(console, ">>> get_ticket(999999)")
    console.print(Text("# live Freshdesk result", style="dim"))
    console.print(JSON(json.dumps(get_ticket(client, 999999))))
    return render(console, "not-found.svg", "Missing ticket: a normal result, not an error")


def rate_limit_image() -> tuple[str, str, str]:
    """Same path as tests/test_rate_limit.py: real server wrapper, HTTP 429 simulated."""
    fake = FreshdeskClient("fictional-shop.freshdesk.com", "FAKE-KEY", sleep_fn=lambda _: None)
    url = "https://fictional-shop.freshdesk.com/api/v2/tickets/1"
    server._client = fake
    try:
        with responses.RequestsMock() as rsps:
            rsps.get(url, status=429, headers={"Retry-After": "40"})
            try:
                server.get_ticket(ticket_id=1)
            except ToolError as exc:
                message = str(exc)
            else:
                sys.exit("Expected a rate-limit ToolError; got a normal result.")
    finally:
        server._client = None
    console = new_console()
    prompt(console, ">>> get_ticket(ticket_id=1)")
    console.print(
        Text("# simulated HTTP 429 with Retry-After: 40 (longer than the 30 s budget)", style="dim")
    )
    console.print(Text("ToolError: ", style="bold red") + Text(message))
    return render(console, "rate-limit.svg", "Rate limiting: the agent is told how long to wait")


def architecture_image() -> tuple[str, str, str]:
    layers = [
        ("AI agent", "Claude Desktop or MCP Inspector"),
        ("MCP server", "server.py: stdio transport, instructions, read-only annotations, errors"),
        ("Tools", "list_tickets, get_ticket, search_tickets, get_ticket_conversations"),
        ("Models", "models.py: plain text only, no emails, truncation flags"),
        ("HTTP client", "client.py: GET only, auth, timeouts, retries, rate limits, pagination"),
        ("Freshdesk API v2", "https://yourcompany.freshdesk.com/api/v2"),
    ]
    parts = []
    for i, (name, detail) in enumerate(layers):
        if i:
            parts.append(Align.center(Text("|\nv", style="dim")))
        parts.append(
            Panel(
                Align.center(Text(detail)),
                title=Text(name, style="bold"),
                border_style="cyan",
                width=80,
            )
        )
    console = new_console()
    console.print(Align.center(Group(*parts)))
    return render(console, "architecture.svg", "Architecture")


def main() -> int:
    client = FreshdeskClient.from_env()
    images = [
        architecture_image(),
        tool_call_image(client),
        not_found_image(client),
        rate_limit_image(),
        tests_image(),
    ]

    forbidden = forbidden_strings()
    for name, text, svg in images:
        for needle in forbidden:
            in_text = needle.lower() in text.lower()
            # SVG markup always contains "@font-face", so "@" is checked in the text only.
            in_svg = needle != "@" and needle.lower() in svg.lower()
            if in_text or in_svg:
                # Never print the matched value: it may be the key or the subdomain.
                print(f"ABORTED: {name} contains forbidden content. No files written.")
                return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, _, svg in images:
        (OUT_DIR / name).write_text(svg, encoding="utf-8", newline="\n")
        print(f"Wrote docs/images/{name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
