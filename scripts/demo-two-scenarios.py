#!/usr/bin/env python3
"""Prepare/check and clean the retained two-scenario product demo.

All mutations go through Java's public API. Cleanup cancels demo meetings so
slots are released while audit/outbox history remains intact; it never deletes
database rows or Docker volumes. The two retained Li Si blocker meetings are
explicitly excluded from cleanup.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BLOCKER_MEETING_NOS = {
    "MTG-DEMO-LISI-20260826-1300",
    "MTG-DEMO-LISI-20260826-1400",
}
REQUIRED_SCENE_TWO_PEOPLE = {1001, 1003, 1010, 1011}
ACTIVE_MEETING_STATUSES = {"CONFIRMED", "PENDING"}
SCENE_TWO_ROOM_CODE = "RD-TEAM-202"


class DemoDataFailure(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise DemoDataFailure(message)


def request_raw(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    actual_headers = {"Accept": "application/json", **(headers or {})}
    payload: bytes | None = None
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        actual_headers["Content-Type"] = "application/json; charset=utf-8"
    request = Request(url, data=payload, headers=actual_headers, method=method)
    try:
        with urlopen(request, timeout=60) as response:
            parsed = json.loads(response.read().decode("utf-8"))
            require(isinstance(parsed, dict), f"{method} {url} returned a non-object")
            return response.status, parsed
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"raw": raw}
        require(isinstance(parsed, dict), f"{method} {url} returned a non-object error")
        return exc.code, parsed


def request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    status, response = request_raw(method, url, headers=headers, body=body)
    if not 200 <= status < 300:
        raise DemoDataFailure(f"{method} {url} returned HTTP {status}: {response}")
    data = response.get("data")
    require(isinstance(data, dict), f"{method} {url} response has no data object")
    return data


def login(public_base: str, username: str) -> dict[str, str]:
    data = request_json(
        "POST",
        f"{public_base}/api/v1/auth/login",
        body={"username": username, "password": "demo-password"},
    )
    token = data.get("accessToken")
    require(isinstance(token, str) and token, f"login for {username} returned no token")
    return {"Authorization": f"Bearer {token}"}


def list_meetings(
    public_base: str,
    headers: dict[str, str],
    start_at: str,
    end_at: str,
) -> list[dict[str, Any]]:
    query = urlencode({"from": start_at, "to": end_at, "page": 1, "size": 100})
    data = request_json(
        "GET", f"{public_base}/api/v1/meetings?{query}", headers=headers
    )
    items = data.get("items")
    require(isinstance(items, list), "meeting list has no items")
    return [item for item in items if isinstance(item, dict)]


def list_rooms(public_base: str, headers: dict[str, str]) -> list[dict[str, Any]]:
    data = request_json("GET", f"{public_base}/api/v1/rooms", headers=headers)
    items = data.get("items")
    require(isinstance(items, list), "room list has no items")
    return [item for item in items if isinstance(item, dict)]


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def participant_ids(meeting: dict[str, Any]) -> set[int]:
    participants = meeting.get("participants")
    if not isinstance(participants, list):
        return set()
    return {
        participant["employeeId"]
        for participant in participants
        if isinstance(participant, dict) and isinstance(participant.get("employeeId"), int)
    }


def demo_reason(meeting: dict[str, Any]) -> str | None:
    if meeting.get("meetingNo") in BLOCKER_MEETING_NOS:
        return None

    organizer_id = meeting.get("organizerId")
    title = meeting.get("title")
    start_at = parse_time(meeting.get("startAt"))
    end_at = parse_time(meeting.get("endAt"))
    if not isinstance(title, str) or start_at is None or end_at is None:
        return None

    if (
        start_at.date().isoformat() == "2026-08-19"
        and 12 <= start_at.hour < 18
        and organizer_id in {1001, 1002}
        and (
            "WeMe1.1" in title
            or title.startswith("演示并发占位")
            or title.startswith("演示并发占用")
            or title in {"并发占位", "并发占用"}
        )
    ):
        return "scene-1"

    if (
        start_at.date().isoformat() == "2026-08-27"
        and start_at.hour >= 9
        and (end_at.hour < 12 or (end_at.hour == 12 and end_at.minute == 0))
        and organizer_id == 1001
        and participant_ids(meeting) == REQUIRED_SCENE_TWO_PEOPLE
    ):
        return "scene-2"
    return None


def cleanup_reason(meeting: dict[str, Any]) -> str | None:
    if meeting.get("status") not in ACTIVE_MEETING_STATUSES:
        return None
    return demo_reason(meeting)


def list_replan_cases(
    public_base: str, headers: dict[str, str]
) -> list[dict[str, Any]]:
    cases: dict[int, dict[str, Any]] = {}
    for status in ("OPEN", "RESOLVED", "RESTORED", "CANCELLED"):
        query = urlencode({"status": status, "page": 1, "size": 100})
        data = request_json(
            "GET", f"{public_base}/api/v1/replan-cases?{query}", headers=headers
        )
        items = data.get("items")
        require(isinstance(items, list), "replan case list has no items")
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("id"), int):
                cases[item["id"]] = item
    return list(cases.values())


def inspect_demo(public_base: str) -> dict[str, Any]:
    admin_headers = login(public_base, "admin")
    zhangsan_headers = login(public_base, "zhangsan")
    august_26 = list_meetings(
        public_base,
        admin_headers,
        "2026-08-26T00:00:00+08:00",
        "2026-08-27T00:00:00+08:00",
    )
    blocker_items = [
        item for item in august_26 if item.get("meetingNo") in BLOCKER_MEETING_NOS
    ]
    blocker_by_no = {item.get("meetingNo"): item for item in blocker_items}
    blocker_ready = set(blocker_by_no) == BLOCKER_MEETING_NOS and all(
        item.get("status") == "CONFIRMED"
        and item.get("organizerId") == 1003
        and any(
            participant.get("employeeId") == 1003
            and participant.get("participantType") == "REQUIRED"
            for participant in item.get("participants", [])
            if isinstance(participant, dict)
        )
        for item in blocker_items
    )
    demo_room = next(
        (room for room in list_rooms(public_base, admin_headers) if room.get("code") == SCENE_TWO_ROOM_CODE),
        None,
    )
    demo_room_ready = (
        isinstance(demo_room, dict)
        and demo_room.get("status") == "ACTIVE"
        and demo_room.get("capacity") == 4
        and demo_room.get("features") == []
    )

    august_19 = list_meetings(
        public_base,
        admin_headers,
        "2026-08-19T00:00:00+08:00",
        "2026-08-20T00:00:00+08:00",
    )
    august_27 = list_meetings(
        public_base,
        admin_headers,
        "2026-08-27T00:00:00+08:00",
        "2026-08-28T00:00:00+08:00",
    )
    cleanup_candidates = [
        {**meeting, "demoScene": reason}
        for meeting in [*august_19, *august_27]
        if (reason := cleanup_reason(meeting)) is not None
    ]
    all_demo_meetings = [
        meeting
        for meeting in [*august_19, *august_27]
        if demo_reason(meeting) is not None
    ]
    cases = list_replan_cases(public_base, zhangsan_headers)
    candidate_ids = {
        item["id"] for item in all_demo_meetings if isinstance(item.get("id"), int)
    }
    for case in cases:
        meeting_id = case.get("meetingId")
        if not isinstance(meeting_id, int) or meeting_id in candidate_ids:
            continue
        meeting = request_json(
            "GET", f"{public_base}/api/v1/meetings/{meeting_id}", headers=admin_headers
        )
        if demo_reason(meeting) is not None:
            candidate_ids.add(meeting_id)
    related_cases = [case for case in cases if case.get("meetingId") in candidate_ids]
    failed_room_ids = sorted(
        {
            room_id
            for case in related_cases
            if isinstance(
                room_id := (
                    case.get("failedRoomId")
                    if isinstance(case.get("failedRoomId"), int)
                    else (case.get("failedRoom") or {}).get("id")
                ),
                int,
            )
        }
    )
    inactive_rooms: list[dict[str, Any]] = []
    for room_id in failed_room_ids:
        room = request_json(
            "GET", f"{public_base}/api/v1/rooms/{room_id}", headers=admin_headers
        )
        if room.get("status") == "INACTIVE":
            inactive_rooms.append(room)

    return {
        "blockerReady": blocker_ready,
        "blockers": blocker_items,
        "demoRoomReady": demo_room_ready,
        "demoRoom": demo_room,
        "cleanupCandidates": cleanup_candidates,
        "relatedReplanCases": related_cases,
        "inactiveRoomsToRestore": inactive_rooms,
    }


def compact_meeting(meeting: dict[str, Any]) -> dict[str, Any]:
    return {
        key: meeting.get(key)
        for key in (
            "id",
            "meetingNo",
            "title",
            "organizerName",
            "roomName",
            "startAt",
            "endAt",
            "status",
            "demoScene",
        )
        if key in meeting
    }


def status(public_base: str) -> int:
    inspected = inspect_demo(public_base)
    result = {
        "ready": (
            inspected["blockerReady"]
            and inspected["demoRoomReady"]
            and not inspected["cleanupCandidates"]
            and not inspected["inactiveRoomsToRestore"]
        ),
        "permanentLiSiBlockers": [
            compact_meeting(item) for item in inspected["blockers"]
        ],
        "residualDemoMeetings": [
            compact_meeting(item) for item in inspected["cleanupCandidates"]
        ],
        "inactiveDemoRooms": [
            {key: room.get(key) for key in ("id", "code", "name", "status", "version")}
            for room in inspected["inactiveRoomsToRestore"]
        ],
        "sceneTwoRoom": {
            key: (inspected["demoRoom"] or {}).get(key)
            for key in ("id", "code", "name", "capacity", "status")
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ready"] else 1


def cleanup(public_base: str, apply: bool) -> int:
    inspected = inspect_demo(public_base)
    candidates = inspected["cleanupCandidates"]
    rooms = inspected["inactiveRoomsToRestore"]
    result: dict[str, Any] = {
        "mode": "apply" if apply else "dry-run",
        "meetings": [compact_meeting(item) for item in candidates],
        "rooms": [
            {key: room.get(key) for key in ("id", "code", "name", "status", "version")}
            for room in rooms
        ],
        "permanentLiSiBlockersPreserved": True,
    }
    if not apply:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    headers_by_organizer = {
        1001: login(public_base, "zhangsan"),
        1002: login(public_base, "admin"),
    }
    cancelled: list[int] = []
    for meeting in candidates:
        meeting_id = meeting.get("id")
        organizer_id = meeting.get("organizerId")
        require(isinstance(meeting_id, int), "cleanup candidate has no meeting id")
        headers = headers_by_organizer.get(organizer_id)
        require(headers is not None, f"unsupported demo organizer: {organizer_id}")
        cancelled_meeting = request_json(
            "DELETE",
            f"{public_base}/api/v1/meetings/{meeting_id}",
            headers=headers,
        )
        require(
            cancelled_meeting.get("status") == "CANCELLED",
            f"meeting {meeting_id} was not cancelled",
        )
        cancelled.append(meeting_id)

    admin_headers = headers_by_organizer[1002]
    restored: list[int] = []
    for room in rooms:
        room_id = room.get("id")
        version = room.get("version")
        require(isinstance(room_id, int), "room to restore has no id")
        require(isinstance(version, int), f"room {room_id} has no version")
        restored_room = request_json(
            "PATCH",
            f"{public_base}/api/v1/admin/rooms/{room_id}/status",
            headers=admin_headers,
            body={"status": "ACTIVE", "expectedVersion": version},
        )
        require(restored_room.get("status") == "ACTIVE", f"room {room_id} not restored")
        restored.append(room_id)

    for headers in headers_by_organizer.values():
        request_json(
            "PATCH",
            f"{public_base}/api/v1/notifications/read-all",
            headers=headers,
        )

    after = inspect_demo(public_base)
    require(after["blockerReady"], "permanent Li Si blocker baseline was damaged")
    require(after["demoRoomReady"], "stable scene-two room baseline is not ready")
    require(not after["cleanupCandidates"], "active demo meetings remain after cleanup")
    require(not after["inactiveRoomsToRestore"], "inactive demo rooms remain after cleanup")
    result["cancelledMeetingIds"] = cancelled
    result["restoredRoomIds"] = restored
    result["ready"] = True
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("status", "cleanup"))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--public-base", default="http://localhost")
    args = parser.parse_args()
    public_base = args.public_base.rstrip("/")
    if args.command == "status":
        require(not args.apply, "--apply is valid only with cleanup")
        return status(public_base)
    return cleanup(public_base, args.apply)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    try:
        raise SystemExit(main())
    except (DemoDataFailure, OSError, TypeError, ValueError) as exc:
        print(f"Two-scenario demo data check failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
