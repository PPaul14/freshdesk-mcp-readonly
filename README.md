# freshdesk-mcp

A **read-only** [Model Context Protocol](https://modelcontextprotocol.io) (MCP) server that lets an
AI agent such as Claude Desktop read tickets from a Freshdesk helpdesk.

The agent can list, look up and search tickets and read their conversation threads. It **cannot**
reply to, update, assign, merge or close tickets: the connector only ever sends HTTP `GET`
requests. Requester emails and phone numbers are never passed to the agent.

- **[CAPABILITIES.md](CAPABILITIES.md)**: what the agent can and cannot do, failure modes, data
  boundaries and evaluation findings.
- **[demo/demo.md](demo/demo.md)**: the demo questions, what the tools return for each, and the
  before/after evaluation.

## Tools

| Tool | Use it for |
|---|---|
| `list_tickets` | Recent tickets, newest first, optionally filtered by status and priority |
| `get_ticket` | One ticket by id, with description, requester name and optionally the first 10 messages |
| `search_tickets` | Tickets matching status, priority, a tag, and created/updated date ranges |
| `get_ticket_conversations` | A ticket's full thread, oldest first, page by page |

The exact tool schemas and server instructions the agent receives are in
[`mcp_tools.json`](mcp_tools.json).

**Limits to know about:**

- No free-text search: you cannot find tickets by words in their subject or body.
- `list_tickets` without `updated_since` only covers tickets created in the last 30 days
  (a Freshdesk default).
- Search returns 30 results per page and at most 10 pages (300 tickets).
- Long descriptions (2,000 chars) and message bodies (1,500 chars) are truncated and flagged.

## Prerequisites

- **Python 3.11 or newer**: check with `python --version`.
- **A Freshdesk account** and its **API key**. A free trial account works.
- **Node.js** (optional): only needed to run MCP Inspector.
- **Claude Desktop** (optional): to use the tools from a chat.

## Setup

### 1. Install

Windows (PowerShell), from the project folder:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

If activation fails with "running scripts is disabled on this system", run
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, then activate again.

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

### 2. Configure your Freshdesk credentials

Copy the example file (`Copy-Item .env.example .env` on Windows, `cp .env.example .env` elsewhere)
and fill in both values:

```dotenv
FRESHDESK_DOMAIN=yourcompany.freshdesk.com
FRESHDESK_API_KEY=your-api-key
```

- `FRESHDESK_DOMAIN` is the host only: no `https://` and no trailing `/`.
- Find your API key in Freshdesk under your **profile picture > Profile Settings > View API Key**.
  If you get a 401 later, also check that API access is enabled for your agent in
  **Admin > Agents**.
- `.env` is git-ignored. Never commit it, and never paste the key into code, issues or logs.

### 3. Check the connection

```bash
python scripts/smoke_test.py
```

This sends a few `GET` requests and prints the ticket count, the first ticket, rate-limit numbers
and a search total, then shows that a wrong key produces a clean `AuthError`. If it prints
`FAILED [...]`, see [Troubleshooting](#troubleshooting).

### 4. (Optional) Add test tickets

An empty helpdesk gives the agent nothing to read. `scripts/seed_tickets.py` creates 15 fictional
tickets with conversations (all requesters use `example.com` addresses):

```bash
python scripts/seed_tickets.py --dry-run   # show the plan, no API calls
python scripts/seed_tickets.py             # create the tickets (~2 minutes)
```

> **This script writes to Freshdesk.** Only run it against a trial or test account. Running it
> twice creates duplicates. Its agent replies trigger emails to `example.com` addresses, which
> bounce harmlessly. The connector itself never imports or calls this script.

## Running the server

The server talks MCP over **stdio**: an MCP client starts it as a subprocess and exchanges
messages through its stdin/stdout. You normally don't start it yourself; if you do
(`freshdesk-mcp` or `python -m freshdesk_mcp.server`), it prints one log line and then waits
silently for a client. That's expected. Press `Ctrl+C` to stop it.

### MCP Inspector (browser UI for trying tools)

```powershell
npx @modelcontextprotocol/inspector "<path-to-repo>\.venv\Scripts\python.exe" -m freshdesk_mcp.server
```

On macOS/Linux use `<path-to-repo>/.venv/bin/python`. Open the URL it prints, click **Connect**,
then **Tools > List Tools**.

### Claude Desktop

Add this to `claude_desktop_config.json` (create the file if it doesn't exist; keep any existing
content and merge the `freshdesk` entry into `mcpServers`):

```json
{
  "mcpServers": {
    "freshdesk": {
      "command": "<path-to-repo>\\.venv\\Scripts\\python.exe",
      "args": ["-m", "freshdesk_mcp.server"]
    }
  }
}
```

Use the **full path** to the virtual environment's Python (on macOS/Linux,
`<path-to-repo>/.venv/bin/python`). In JSON, Windows backslashes must be doubled. No `env` block is
needed: the server reads `.env` from the project folder no matter where Claude Desktop starts it.

Where the config file lives:

| Install | Path |
|---|---|
| Windows (installer from claude.ai) | `%APPDATA%\Claude\claude_desktop_config.json` |
| Windows (Microsoft Store) | `%LOCALAPPDATA%\Packages\Claude_<id>\LocalCache\Roaming\Claude\claude_desktop_config.json` |
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |

Then **fully quit** Claude Desktop (from the system tray / menu bar, not just the window) and
reopen it. The four tools should appear in the tools menu.

## Development

```bash
pytest                      # 51 tests; all HTTP is mocked, no Freshdesk account needed
ruff format . && ruff check .
```

If you change a tool's signature or description, or the server instructions,
`tests/test_tool_specs.py` will fail until you regenerate and commit the spec file:

```bash
python scripts/export_tool_specs.py
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `[config_error] Missing or empty environment variable(s)` | `.env` is missing or a value is empty. It must sit next to `pyproject.toml`. |
| `.env` is filled in but values still look empty | A variable with the same name already set in your shell or system environment wins over `.env`. In PowerShell check `Test-Path env:FRESHDESK_API_KEY`; remove it with `Remove-Item env:FRESHDESK_API_KEY` or open a new terminal. |
| `[auth_failed] ... (HTTP 401)` | Wrong API key, or API access is disabled for your agent in **Admin > Agents**. |
| `[rate_limited]` | Freshdesk's per-minute limit was hit (50/min on trials). The client already retries; wait a minute. |
| Tools don't appear in Claude Desktop | Check the config path for your install (table above), that the `command` path exists, and that you fully restarted the app. Server logs are in the `logs` folder next to the config file (`mcp-server-freshdesk.log`). |

The server logs to **stderr only**, because stdout carries the MCP protocol.

## Project layout

```
src/freshdesk_mcp/
  server.py        MCP server: tool registration, instructions, error mapping
  client.py        Read-only HTTP client: auth, timeouts, retries, rate limits, pagination
  errors.py        Typed errors (auth_failed, not_found, rate_limited, ...)
  models.py        What the agent sees: plain text, no emails, truncation flags
  tools/           The four tool functions and shared input validation
scripts/
  smoke_test.py         Live connection check (read-only)
  seed_tickets.py       Creates fictional test tickets (WRITES to Freshdesk)
  seed_data.json        The fictional tickets
  export_tool_specs.py  Regenerates mcp_tools.json
tests/                  pytest suite (HTTP mocked with `responses`)
mcp_tools.json          Generated: server instructions + tool schemas
```
