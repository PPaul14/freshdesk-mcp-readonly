"""The committed mcp_tools.json must match what the server actually advertises."""

import json
from pathlib import Path

from freshdesk_mcp.server import tool_specs

SPEC_FILE = Path(__file__).resolve().parents[1] / "mcp_tools.json"


def test_committed_tool_specs_match_server() -> None:
    # Compare parsed JSON, not raw text, so Git's line-ending conversion can't cause failures.
    committed = json.loads(SPEC_FILE.read_text(encoding="utf-8"))
    assert committed == tool_specs(), (
        "mcp_tools.json is out of date. Run: python scripts/export_tool_specs.py"
    )
