"""Write mcp_tools.json: the server's tool list exactly as an MCP client sees it.

Usage (from the project root):
    python scripts/export_tool_specs.py

Lists tools in-process; makes no Freshdesk calls and needs no .env.
Re-run after changing any tool signature or description, then commit the file.
tests/test_tool_specs.py fails if the committed file is out of date.
"""

from pathlib import Path

from freshdesk_mcp.server import tool_specs_json

OUTPUT = Path(__file__).resolve().parents[1] / "mcp_tools.json"


def main() -> None:
    # newline="\n" so the file is identical on Windows and Linux.
    OUTPUT.write_text(tool_specs_json(), encoding="utf-8", newline="\n")
    print(f"Wrote {OUTPUT.name}")


if __name__ == "__main__":
    main()
