#!/usr/bin/env python3
"""Day 6 public-API smoke for the browser-facing scheduling surface.

This script deliberately talks only to Java's public `/api/v1/**` endpoints.
It proves the data and SSE contracts consumed by the Day 6 Vue UI, creates one
fictional manual meeting in a discovered available slot, and always cancels it.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import datetime, timedelta
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


class SmokeFailure(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def request_raw(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    accept: str = "application/json",
) -> tuple[int, dict[str, Any], dict[str, str]]:
    actual_headers = {"Accept": accept, **(headers or {})}
    payload: bytes | None = None
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        actual_headers["Content-Type"] = "application/json; charset=utf-8"

    request = Request(url, data=payload, headers=actual_headers, method=method)
    try:
        with urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
            parsed = json.loads(raw) if raw else {}
            require(isinstance(parsed, dict), f"{method} {url} did not return a JSON object")
            return response.status, parsed, dict(response.headers.items())
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"raw": raw}
        require(isinstance(parsed, dict), f"{method} {url} returned a non-object error")
        return exc.code, parsed, dict(exc.headers.items())


def request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    status, response, response_headers = request_raw(method, url, headers=headers, body=body)
    if not 200 <= status < 300:
        raise SmokeFailure(f"{method} {url} returned HTTP {status}: {response}")
    return response, response_headers


def request_sse(
    url: str, *, headers: dict[str, str], body: dict[str, Any]
) -> tuple[str, list[tuple[str, dict[str, Any]]]]:
    actual_headers = {"Accept": "text/event-stream", **headers, "Content-Type": "application/json"}
    request = Request(
        url,
        data=json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        headers=actual_headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=90) as stream:
            run_id = stream.headers.get("X-Run-Id", "")
            raw = stream.read().decode("utf-8")
    except HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        raise SmokeFailure(f"agent SSE returned HTTP {exc.code}: {body_text}") from exc

    events: list[tuple[str, dict[str, Any]]] = []
    for block in raw.replace("\r\n", "\n").split("\n\n"):
        if not block.strip():
            continue
        name = "message"
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data_lines.append(line.removeprefix("data:").strip())
        require(data_lines, f"SSE event {name!r} has no data")
        parsed = json.loads("\n".join(data_lines))
        require(isinstance(parsed, dict), f"SSE event {name!r} is not an object")
        events.append((name, parsed))
    return run_id, events


def event_data(events: list[tuple[str, dict[str, Any]]], name: str) -> dict[str, Any]:
    matches = [data for event_name, data in events if event_name == name]
    require(bool(matches), f"missing SSE event {name}")
    return matches[-1]


def find_available_interval(
    public_base: str, headers: dict[str, str], rooms: list[dict[str, Any]]
) -> tuple[int, str, str, dict[str, Any]]:
    zone = ZoneInfo("Asia/Shanghai")
    start_day = (datetime.now(zone) + timedelta(days=7)).replace(
        hour=9, minute=0, second=0, microsecond=0
    )
    for day_offset in range(7):
        window_start = start_day + timedelta(days=day_offset)
        window_end = window_start.replace(hour=18)
        query = urlencode({"from": window_start.isoformat(), "to": window_end.isoformat()})
        for room in rooms:
            room_id = room.get("id")
            if not isinstance(room_id, int):
                continue
            response, _ = request_json(
                "GET",
                f"{public_base}/api/v1/rooms/{room_id}/availability?{query}",
                headers=headers,
            )
            availability = response.get("data")
            require(isinstance(availability, dict), "availability response lacks data")
            slots = availability.get("availableSlots")
            require(isinstance(slots, list), "availability response lacks availableSlots")
            for current, following in zip(slots, slots[1:], strict=False):
                if not isinstance(current, dict) or not isinstance(following, dict):
                    continue
                if (
                    current.get("available") is True
                    and following.get("available") is True
                    and current.get("endAt") == following.get("startAt")
                    and isinstance(current.get("startAt"), str)
                    and isinstance(following.get("endAt"), str)
                ):
                    return room_id, current["startAt"], following["endAt"], availability
    raise SmokeFailure("no two consecutive public availability slots found")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-base", default="http://localhost")
    args = parser.parse_args()
    public_base = args.public_base.rstrip("/")

    login, _ = request_json(
        "POST",
        f"{public_base}/api/v1/auth/login",
        body={"username": "zhangsan", "password": "demo-password"},
    )
    employee_headers = {"Authorization": f"Bearer {login['data']['accessToken']}"}

    rooms_response, _ = request_json("GET", f"{public_base}/api/v1/rooms", headers=employee_headers)
    rooms_data = rooms_response.get("data")
    require(isinstance(rooms_data, dict), "room list has no data")
    rooms = rooms_data.get("items")
    require(isinstance(rooms, list) and rooms, "employee has no active rooms")
    require(all(isinstance(room, dict) and room.get("status") == "ACTIVE" for room in rooms), "employee received an inactive room")

    room_id, start_at, end_at, availability = find_available_interval(
        public_base, employee_headers, rooms
    )
    require(availability.get("roomId") == room_id, "availability response roomId mismatch")

    # RBAC is asserted without creating data: access is rejected before the body reaches the handler.
    forbidden_status, forbidden, _ = request_raw(
        "POST",
        f"{public_base}/api/v1/admin/rooms",
        headers=employee_headers,
        body={
            "code": f"DAY6-FORBIDDEN-{uuid.uuid4().hex[:8]}",
            "name": "forbidden",
            "building": "test",
            "floor": "1F",
            "capacity": 2,
            "roomType": "STANDARD",
            "isHot": False,
            "featureCodes": [],
        },
    )
    require(forbidden_status == 403 and forbidden.get("code") == "FORBIDDEN", "admin room RBAC is not enforced")

    manual_meeting_id: int | None = None
    try:
        create_body = {
            "title": f"Day 6 manual smoke {uuid.uuid4().hex[:8]}",
            "meetingType": "ARCHITECTURE_REVIEW",
            "roomId": room_id,
            "startAt": start_at,
            "endAt": end_at,
            "requiredParticipantIds": [],
            "optionalParticipantIds": [],
        }
        created, _ = request_json(
            "POST",
            f"{public_base}/api/v1/meetings",
            headers={**employee_headers, "Idempotency-Key": f"idem-day6-{uuid.uuid4().hex}"},
            body=create_body,
        )
        created_meeting = created.get("data")
        require(isinstance(created_meeting, dict), "manual create has no data")
        manual_meeting_id = created_meeting.get("id")
        require(isinstance(manual_meeting_id, int) and manual_meeting_id > 0, "manual create has no id")
        require(created_meeting.get("status") == "CONFIRMED", "manual meeting is not confirmed")

        query = urlencode({"from": start_at, "to": end_at, "page": 1, "size": 20})
        listed, _ = request_json(
            "GET", f"{public_base}/api/v1/meetings?{query}", headers=employee_headers
        )
        items = listed.get("data", {}).get("items")
        require(
            isinstance(items, list) and any(item.get("id") == manual_meeting_id for item in items if isinstance(item, dict)),
            "manual meeting is absent from the user's list",
        )

        update_body = {**create_body, "title": f"{create_body['title']} updated", "expectedVersion": created_meeting["version"]}
        updated, _ = request_json(
            "PUT",
            f"{public_base}/api/v1/meetings/{manual_meeting_id}",
            headers=employee_headers,
            body=update_body,
        )
        require(updated.get("data", {}).get("title") == update_body["title"], "manual update did not persist")
    finally:
        if manual_meeting_id is not None:
            cancelled, _ = request_json(
                "DELETE",
                f"{public_base}/api/v1/meetings/{manual_meeting_id}",
                headers=employee_headers,
            )
            require(cancelled.get("data", {}).get("status") == "CANCELLED", "manual cleanup did not cancel")

    # The same public SSE/recovery/trace data is what the browser uses; REJECT keeps this smoke
    # free of Agent WRITE side effects while proving the candidate, HITL and trace views.
    trace_id = f"trc_day6_{uuid.uuid4().hex}"
    run_id, events = request_sse(
        f"{public_base}/api/v1/agent/runs/stream",
        headers={**employee_headers, "X-Trace-Id": trace_id},
        body={
            "threadId": None,
            "message": "下周三下午帮张三安排一个90分钟架构评审，要大屏",
            "clientRequestId": str(uuid.uuid4()),
        },
    )
    require(bool(run_id), "Java SSE proxy returned no run id")
    require(events[0][0] == "run.started", "SSE did not start with run.started")
    require(events[-1][0] == "hitl.required", "SSE did not pause for HITL")
    candidates = event_data(events, "plan.candidates").get("candidates")
    require(isinstance(candidates, list) and 1 <= len(candidates) <= 3, "invalid candidate list")
    confirmation_token = event_data(events, "hitl.required").get("confirmationToken")
    require(isinstance(confirmation_token, str) and confirmation_token, "HITL token missing")

    rejected_run_id, rejection_events = request_sse(
        f"{public_base}/api/v1/agent/runs/{quote(run_id, safe='')}/resume",
        headers={**employee_headers, "X-Trace-Id": f"trc_day6_reject_{uuid.uuid4().hex}"},
        body={
            "action": "REJECT",
            "confirmationToken": confirmation_token,
            "editedDraft": None,
            "feedback": None,
        },
    )
    require(rejected_run_id == run_id, "REJECT changed run id")
    require(rejection_events[-1][0] == "run.completed", "REJECT did not complete run")

    trace_response, trace_headers = request_json(
        "GET",
        f"{public_base}/api/v1/agent/runs/{quote(run_id, safe='')}/trace",
        headers=employee_headers,
    )
    require(
        "no-store" in trace_headers.get("Cache-Control", trace_headers.get("Cache-control", "")).lower(),
        "trace response is cacheable",
    )
    trace_data = trace_response.get("data")
    require(isinstance(trace_data, dict) and isinstance(trace_data.get("steps"), list), "trace is incomplete")
    require(confirmation_token not in json.dumps(trace_data, ensure_ascii=False), "trace leaked confirmation token")

    admin_login, _ = request_json(
        "POST",
        f"{public_base}/api/v1/auth/login",
        body={"username": "admin", "password": "demo-password"},
    )
    admin_headers = {"Authorization": f"Bearer {admin_login['data']['accessToken']}"}
    admin_rooms, _ = request_json("GET", f"{public_base}/api/v1/rooms", headers=admin_headers)
    admin_items = admin_rooms.get("data", {}).get("items")
    require(isinstance(admin_items, list) and len(admin_items) >= len(rooms), "admin room list is invalid")
    detail, _ = request_json(
        "GET", f"{public_base}/api/v1/rooms/{room_id}", headers=admin_headers
    )
    require(detail.get("data", {}).get("version") is not None, "room detail lacks optimistic version")

    print(
        json.dumps(
            {
                "day6PublicSurface": "PASS",
                "activeRoomCount": len(rooms),
                "manualMeeting": "created-updated-cancelled",
                "agentSse": "candidates-hitl-reject-trace",
                "roomAdminRbac": "PASS",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyError, OSError, SmokeFailure, TypeError, ValueError) as exc:
        print(f"Day 6 smoke failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
