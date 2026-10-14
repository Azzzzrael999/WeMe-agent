"""Regenerate the Java classpath contract from the Python business argument schemas.

Run from agent-service: uv run python ../scripts/generate_mcp_catalog.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent-service"))

from app.tools.java import (
    CancellationPreviewInput,
    ConfirmBookingResponse,
    ConfirmInput,
    CreateBookingDraftInput,
    CreateBookingDraftResponse,
    CreateCancellationPreviewResponse,
    CreateRescheduleDraftResponse,
    FreeBusyInput,
    RecentMeetingInput,
    RescheduleDraftInput,
    ResolveEmployeesInput,
    ResolveParticipantScopeInput,
    SearchRoomsInput,
)

INPUTS = {
    "resolve_employees": ResolveEmployeesInput,
    "get_employee_free_busy": FreeBusyInput,
    "search_available_rooms": SearchRoomsInput,
    "get_recent_meeting": RecentMeetingInput,
    "resolve_participant_scope": ResolveParticipantScopeInput,
    "create_booking_draft": CreateBookingDraftInput,
    "create_reschedule_draft": RescheduleDraftInput,
    "create_cancellation_preview": CancellationPreviewInput,
    "confirm_booking": ConfirmInput,
    "confirm_reschedule": ConfirmInput,
    "confirm_cancellation": ConfirmInput,
}
OUTPUTS = {
    "create_booking_draft": CreateBookingDraftResponse,
    "create_reschedule_draft": CreateRescheduleDraftResponse,
    "create_cancellation_preview": CreateCancellationPreviewResponse,
    "confirm_booking": ConfirmBookingResponse,
    "confirm_reschedule": ConfirmBookingResponse,
    "confirm_cancellation": ConfirmBookingResponse,
}


def object_schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def output_schema(name: str) -> dict[str, Any]:
    if name in OUTPUTS:
        data = OUTPUTS[name].model_json_schema(by_alias=True, mode="serialization")
    else:
        arrays = {
            "resolve_employees": ["employees", "unresolvedNames"],
            "get_employee_free_busy": ["employees"],
            "search_available_rooms": ["rooms"],
            "get_recent_meeting": ["meetings"],
            "resolve_participant_scope": ["members"],
        }[name]
        properties = {field: {"type": "array"} for field in arrays}
        if name == "get_recent_meeting":
            properties["roomFeaturesByMeetingId"] = {"type": "object"}
        if name == "resolve_participant_scope":
            properties.update(scope={"type": "string"}, scopeName={"type": "string"})
        data = object_schema(properties, list(properties))
    defs = data.pop("$defs", {})
    failure = object_schema(
        {
            "ok": {"const": False},
            "error": object_schema(
                {
                    "code": {"type": "string"},
                    "message": {"type": "string"},
                    "details": {
                        "type": "array",
                        "items": object_schema(
                            {
                                "field": {"type": "string"},
                                "reason": {"type": "string"},
                            },
                            ["field", "reason"],
                        ),
                    },
                },
                ["code", "message", "details"],
            ),
        },
        ["ok", "error"],
    )
    return {
        "type": "object",
        "$defs": defs,
        "oneOf": [object_schema({"ok": {"const": True}, "data": data}, ["ok", "data"]), failure],
    }


def build_catalog() -> dict[str, Any]:
    return {
        name: {
            "description": name.replace("_", " ") + " (trusted business workflow)",
            "riskLevel": "WRITE"
            if name.startswith("confirm_")
            else ("DRAFT" if name.startswith("create_") else "READ"),
            "inputSchema": model.model_json_schema(by_alias=True, mode="serialization"),
            "outputSchema": output_schema(name),
        }
        for name, model in INPUTS.items()
    }


if __name__ == "__main__":
    target = Path(__file__).resolve().parents[1] / (
        "business-service/src/main/resources/mcp/tool-catalog.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(build_catalog(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Generated {len(INPUTS)} tool contracts")
