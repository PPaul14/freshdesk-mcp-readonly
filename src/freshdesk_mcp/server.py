"""MCP server exposing four read-only Freshdesk ticket tools over stdio.

Run with either:
    python -m freshdesk_mcp.server
    freshdesk-mcp

stdout carries the MCP protocol, so nothing here may print; all logging goes to stderr.
"""

import asyncio
import json
import logging
import sys
import threading
from collections.abc import Callable
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from freshdesk_mcp.client import SEARCH_MAX_PAGE, FreshdeskClient
from freshdesk_mcp.errors import FreshdeskError
from freshdesk_mcp.tools import get_ticket as get_ticket_tool
from freshdesk_mcp.tools import get_ticket_conversations as conversations_tool
from freshdesk_mcp.tools import list_tickets as list_tickets_tool
from freshdesk_mcp.tools import search_tickets as search_tickets_tool
from freshdesk_mcp.tools._shared import TOOL_MAX_PER_PAGE

logger = logging.getLogger("freshdesk_mcp")

INSTRUCTIONS = (
    "Read-only access to a Freshdesk helpdesk. You cannot reply to, update, assign, merge or "
    "close tickets. Requester emails are deliberately not provided. If the data does not answer "
    "the question, say so instead of guessing. "
    "Searches can only filter by status, priority, tag and dates. Never offer to search by "
    "requester, email or words in the subject or body. "
    "Never offer to perform write actions (reply, close, merge, update) through any other tool "
    "such as a browser. Draft the text and tell the user a human must do it in Freshdesk. "
    "Do not infer emotions, sentiment, intent or churn risk. You may report factual signals "
    "(overdue, no reply, number of follow-ups) and must say they are not a prediction."
)

LIST_TICKETS_DESCRIPTION = (
    "List recent tickets, newest first, optionally filtered by status and priority. Use for "
    "broad questions like 'what's open right now?' or 'show recent urgent tickets'. Without "
    "updated_since, only tickets created in the last 30 days are returned. Do NOT use to find "
    "tickets by words in their subject or body; that is not supported. Check meta.has_more and "
    "meta.notes before concluding you have seen everything."
)
GET_TICKET_DESCRIPTION = (
    "Get one ticket by its numeric id, with full description and requester name. Set "
    "include_conversations=true to also get up to the first 10 messages. Use when the user "
    "names a ticket id or you need the details of one ticket. If found is false, the ticket "
    "does not exist: say so, do not guess its contents. If meta.truncated is true, call "
    "get_ticket_conversations to read the rest of the thread."
)
SEARCH_TICKETS_DESCRIPTION = (
    "Find tickets by structured fields: status, priority, tag, and created/updated date ranges "
    "(YYYY-MM-DD). Use for questions like 'urgent tickets created this week' or 'tickets tagged "
    "refunds'. Cannot match free text in the subject or body. Do NOT use for a single known "
    "id; use get_ticket. Returns 30 per page, at most 10 pages. An empty list means no "
    "matches, not an error."
)
GET_TICKET_CONVERSATIONS_DESCRIPTION = (
    "Read a ticket's full message thread, oldest first, paginated. Use when you need the whole "
    "conversation, e.g. to find the root cause of a problem that only emerges later in the "
    "thread. from_customer=true marks customer messages. is_private_note=true marks internal "
    "agent notes: never quote these to a customer. If meta.has_more is true, call again with "
    "the next page before concluding."
)

Status = Literal["open", "pending", "resolved", "closed"]
Priority = Literal["low", "medium", "high", "urgent"]
IsoDate = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
TicketId = Annotated[int, Field(ge=1, description="Numeric Freshdesk ticket id.")]

# Hints for the client: these tools only read, and they talk to an external system.
READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True
)

server = MCPServer(name="freshdesk", instructions=INSTRUCTIONS)

_client: FreshdeskClient | None = None
# The SDK runs sync tools in worker threads, so two first calls could race to build the client.
_client_lock = threading.Lock()


def get_client() -> FreshdeskClient:
    """Build the client on first use, so a missing .env is a tool error, not a launch crash.

    from_env() loads the .env next to pyproject.toml, not the working directory, because
    Claude Desktop starts the server from a different folder. A failed build is not
    cached: fixing .env makes the next call succeed without restarting.
    """
    global _client
    with _client_lock:
        if _client is None:
            _client = FreshdeskClient.from_env()
        return _client


def _run(tool_name: str, fn: Callable[..., dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    """Call a tool function and turn every failure into a clean ToolError."""
    try:
        return fn(get_client(), **kwargs)
    except FreshdeskError as exc:
        raise ToolError(f"[{exc.code}] {exc.message}") from None
    except Exception:
        # Full traceback to stderr for us; only a generic message for the model.
        logger.exception("Unexpected error in tool %s", tool_name)
        raise ToolError("[internal_error] Unexpected error; see server logs") from None


@server.tool(description=LIST_TICKETS_DESCRIPTION, annotations=READ_ONLY)
def list_tickets(
    status: Annotated[Status | None, Field(description="Only tickets with this status.")] = None,
    priority: Annotated[
        Priority | None, Field(description="Only tickets with this priority.")
    ] = None,
    updated_since: Annotated[
        IsoDate | None,
        Field(
            description="YYYY-MM-DD. Tickets updated on or after this date, "
            "including ones created more than 30 days ago."
        ),
    ] = None,
    page: Annotated[int, Field(ge=1, description="Page number, starting at 1.")] = 1,
    per_page: Annotated[
        int,
        Field(
            ge=1,
            le=TOOL_MAX_PER_PAGE,
            description="Tickets per page. Ignored (fixed at 30) when status or priority is set.",
        ),
    ] = 20,
) -> dict[str, Any]:
    return _run(
        "list_tickets",
        list_tickets_tool.list_tickets,
        status=status,
        priority=priority,
        updated_since=updated_since,
        page=page,
        per_page=per_page,
    )


@server.tool(description=GET_TICKET_DESCRIPTION, annotations=READ_ONLY)
def get_ticket(
    ticket_id: TicketId,
    include_conversations: Annotated[
        bool, Field(description="Also return up to the first 10 messages of the thread.")
    ] = False,
) -> dict[str, Any]:
    return _run(
        "get_ticket",
        get_ticket_tool.get_ticket,
        ticket_id=ticket_id,
        include_conversations=include_conversations,
    )


@server.tool(description=SEARCH_TICKETS_DESCRIPTION, annotations=READ_ONLY)
def search_tickets(
    status: Annotated[
        list[Status] | None, Field(description="Match any of these statuses.")
    ] = None,
    priority: Annotated[
        list[Priority] | None, Field(description="Match any of these priorities.")
    ] = None,
    tag: Annotated[
        str | None,
        Field(
            max_length=64,
            description="One exact tag, e.g. 'refunds'. Letters, digits, space, _ . - only.",
        ),
    ] = None,
    created_after: Annotated[IsoDate | None, Field(description="YYYY-MM-DD, inclusive.")] = None,
    created_before: Annotated[IsoDate | None, Field(description="YYYY-MM-DD, inclusive.")] = None,
    updated_after: Annotated[IsoDate | None, Field(description="YYYY-MM-DD, inclusive.")] = None,
    page: Annotated[int, Field(ge=1, le=SEARCH_MAX_PAGE, description="Page number, 1 to 10.")] = 1,
) -> dict[str, Any]:
    return _run(
        "search_tickets",
        search_tickets_tool.search_tickets,
        status=status,
        priority=priority,
        tag=tag,
        created_after=created_after,
        created_before=created_before,
        updated_after=updated_after,
        page=page,
    )


@server.tool(description=GET_TICKET_CONVERSATIONS_DESCRIPTION, annotations=READ_ONLY)
def get_ticket_conversations(
    ticket_id: TicketId,
    page: Annotated[int, Field(ge=1, description="Page number, starting at 1.")] = 1,
    per_page: Annotated[
        int, Field(ge=1, le=TOOL_MAX_PER_PAGE, description="Messages per page.")
    ] = 20,
) -> dict[str, Any]:
    return _run(
        "get_ticket_conversations",
        conversations_tool.get_ticket_conversations,
        ticket_id=ticket_id,
        page=page,
        per_page=per_page,
    )


def server_spec() -> dict[str, Any]:
    """Instructions and tool list exactly as an MCP client sees them (used for mcp_tools.json)."""
    tools = asyncio.run(server.list_tools())
    specs = []
    for tool in tools:
        spec = {
            "name": tool.name,
            "description": tool.description,
            "inputSchema": tool.input_schema,
        }
        if tool.output_schema is not None:
            spec["outputSchema"] = tool.output_schema
        specs.append(spec)
    return {"instructions": server.instructions, "tools": specs}


def server_spec_json() -> str:
    return json.dumps(server_spec(), indent=2, sort_keys=True) + "\n"


def main() -> None:
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info("Starting Freshdesk MCP server on stdio")
    server.run("stdio")


if __name__ == "__main__":
    main()
