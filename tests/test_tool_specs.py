"""The committed mcp_tools.json must match what the server actually advertises."""

import json
from pathlib import Path

from freshdesk_mcp.server import server_spec

SPEC_FILE = Path(__file__).resolve().parents[1] / "mcp_tools.json"
FIX_HINT = "mcp_tools.json is out of date. Run: python scripts/export_tool_specs.py"


def _committed() -> dict:
    # Compare parsed JSON, not raw text, so Git's line-ending conversion can't cause failures.
    return json.loads(SPEC_FILE.read_text(encoding="utf-8"))


def test_committed_instructions_match_server() -> None:
    assert _committed().get("instructions") == server_spec()["instructions"], FIX_HINT


def test_committed_tool_specs_match_server() -> None:
    assert _committed() == server_spec(), FIX_HINT
