"""Write mcp_tools.json: the server's instructions and tool list as an MCP client sees them.

Usage (from the project root):
    python scripts/export_tool_specs.py

Lists tools in-process; makes no Freshdesk calls and needs no .env.
Re-run after changing the instructions or any tool signature or description, then commit.
tests/test_tool_specs.py fails if the committed file is out of date.
"""

from pathlib import Path

from freshdesk_mcp.server import server_spec_json

OUTPUT = Path(__file__).resolve().parents[1] / "mcp_tools.json"


def main() -> None:
    # newline="\n" so the file is identical on Windows and Linux.
    OUTPUT.write_text(server_spec_json(), encoding="utf-8", newline="\n")
    print(f"Wrote {OUTPUT.name}")


if __name__ == "__main__":
    main()
