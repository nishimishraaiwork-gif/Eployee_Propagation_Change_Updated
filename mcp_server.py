"""Optional MCP server exposing safe tools for the propagation prototype.

Install the optional dependency with: python -m pip install "mcp[cli]"
Run from this directory with: python mcp_server.py
This server uses in-memory mock systems only; it does not connect to vendor APIs.
"""
from __future__ import annotations

import json
from typing import Any

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # helpful error if optional dependency isn't installed
    raise SystemExit(
        'MCP support is optional. Install it with: python -m pip install "mcp[cli]"'
    ) from exc

from main import make_engine

mcp = FastMCP("employee-change-propagation")
engine, destinations, audit, analyzer = make_engine()


@mcp.tool()
def process_employee_change(event_json: str) -> dict[str, Any]:
    """Validate and propagate one employee-change event to mock destinations.

    Args:
        event_json: JSON string containing event_id and an employee object with
            employee_id, name, email, location, and start_date (YYYY-MM-DD).

    This tool writes only to in-memory mock systems. Invalid records are rejected.
    """
    try:
        event = json.loads(event_json)
    except json.JSONDecodeError as exc:
        return {"status": "rejected", "reason": f"Invalid JSON: {exc.msg}"}
    if not isinstance(event, dict):
        return {"status": "rejected", "reason": "Event must be a JSON object"}
    return engine.process_event(event)


@mcp.tool()
def get_propagation_status(event_id: str) -> dict[str, Any]:
    """Return audit outcomes for an event ID; does not expose employee PII."""
    matching = [entry for entry in audit.entries if entry.get("event_id") == event_id]
    if not matching:
        return {"event_id": event_id, "found": False, "message": "No audit entries found"}

    destination_status: dict[str, str] = {}
    for entry in matching:
        destination = entry.get("destination")
        if destination:
            destination_status[destination] = str(entry.get("status", "unknown"))
    overall = next(
        (str(entry.get("status")) for entry in reversed(matching)
         if entry.get("status") in {"success", "partial_failure", "rejected", "duplicate"}),
        "in_progress_or_unknown",
    )
    return {
        "event_id": event_id,
        "found": True,
        "overall_status": overall,
        "destination_status": destination_status,
        "audit_entry_count": len(matching),
    }


@mcp.tool()
def list_mock_destinations() -> dict[str, Any]:
    """List the mock destination names and their current record counts."""
    return {
        "mode": "mock",
        "destinations": {
            key: {"name": destination.name, "record_count": len(destination.records)}
            for key, destination in destinations.items()
        },
        "exception_analyzer": analyzer.__class__.__name__,
    }


if __name__ == "__main__":
    # MCP stdio transport is intended for local MCP clients.
    mcp.run(transport="stdio")
