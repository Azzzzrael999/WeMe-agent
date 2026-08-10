#!/usr/bin/env python3
"""Public-API smoke for room failure and exception replanning.

The script talks only to Java's `/api/v1/**` surface. It creates one fictional
future meeting, disables its room, proves case/notification isolation, applies a
same-time replacement, then restores the room and cancels the meeting. It never
touches databases, internal Tools, or Docker volumes.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from contextlib import suppress
from datetime import datetime, timedelta
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
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
) -> tuple[int, dict[str, Any]]:
    actual_headers = {"Accept": "application/json", **(headers or {})}
    payload: bytes | None = None
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
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
        raise SmokeFailure(f"{method} {url} returned HTTP {status}: {response}")
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


def feature_codes(room: dict[str, Any]) -> set[str]:
    features = room.get("features")
    if not isinstance(features, list):
        return set()
    return {
        feature["code"]
        for feature in features
        if isinstance(feature, dict) and isinstance(feature.get("code"), str)
    }


def one_hour_starts(
    public_base: str,
    headers: dict[str, str],
    room_id: int,
    day: datetime,
) -> dict[str, str]:
    query = urlencode(
        {
            "from": day.replace(hour=9).isoformat(),
            "to": day.replace(hour=18).isoformat(),
        }
    )
    slots = request_json(
        "GET",
        f"{public_base}/api/v1/rooms/{room_id}/availability?{query}",
        headers=headers,
    ).get("availableSlots")
    require(isinstance(slots, list), "room availability has no slots")
    intervals: dict[str, str] = {}
    for left, right in zip(slots, slots[1:], strict=False):
        if (
            isinstance(left, dict)
            and isinstance(right, dict)
            and left.get("available") is True
            and right.get("available") is True
            and left.get("endAt") == right.get("startAt")
            and isinstance(left.get("startAt"), str)
            and isinstance(right.get("endAt"), str)
        ):
            intervals[left["startAt"]] = right["endAt"]
    return intervals


def find_room_pair_and_interval(
    public_base: str,
    admin_headers: dict[str, str],
    rooms: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    active = [
        room
        for room in rooms
        if room.get("status") == "ACTIVE"
        and room.get("isHot") is False
        and isinstance(room.get("id"), int)
        and isinstance(room.get("capacity"), int)
        and room["capacity"] >= 2
    ]
    pairs = [
        (source, alternative)
        for source in active
        for alternative in active
        if source["id"] != alternative["id"]
        and alternative["capacity"] >= 2
        and feature_codes(source).issubset(feature_codes(alternative))
    ]
    require(bool(pairs), "no compatible active room pair exists")
    zone = ZoneInfo("Asia/Shanghai")
    first_day = (datetime.now(zone) + timedelta(days=8)).replace(
        hour=9, minute=0, second=0, microsecond=0
    )
    for day_offset in range(14):
        day = first_day + timedelta(days=day_offset)
        for source, alternative in pairs:
            source_slots = one_hour_starts(public_base, admin_headers, source["id"], day)
            alternative_slots = one_hour_starts(
                public_base, admin_headers, alternative["id"], day
            )
            common = sorted(set(source_slots).intersection(alternative_slots))
            if common:
                start_at = common[0]
                require(
                    source_slots[start_at] == alternative_slots[start_at],
                    "common interval end mismatch",
                )
                return source, alternative, start_at, source_slots[start_at]
    raise SmokeFailure("no common one-hour room interval found")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-base", default="http://localhost")
    args = parser.parse_args()
    public_base = args.public_base.rstrip("/")

    admin_headers = login(public_base, "admin")
    organizer_headers = login(public_base, "zhangsan")
    participant_headers = login(public_base, "lisi")
    room_list = request_json("GET", f"{public_base}/api/v1/rooms", headers=admin_headers)
    rooms = room_list.get("items")
    require(isinstance(rooms, list), "room list has no items")
    source, expected_alternative, start_at, end_at = find_room_pair_and_interval(
        public_base, admin_headers, [room for room in rooms if isinstance(room, dict)]
    )

    meeting_id: int | None = None
    source_disabled = False
    try:
        created = request_json(
            "POST",
            f"{public_base}/api/v1/meetings",
            headers={
                **organizer_headers,
                "Idempotency-Key": f"idem-replan-smoke-{uuid.uuid4().hex}",
            },
            body={
                "title": f"异常重排验收 {uuid.uuid4().hex[:8]}",
                "meetingType": "GENERAL",
                "roomId": source["id"],
                "startAt": start_at,
                "endAt": end_at,
                "requiredParticipantIds": [1003],
                "optionalParticipantIds": [],
            },
        )
        meeting_id = created.get("id")
        require(isinstance(meeting_id, int), "created meeting has no id")

        disabled = request_json(
            "PATCH",
            f"{public_base}/api/v1/admin/rooms/{source['id']}/status",
            headers=admin_headers,
            body={
                "status": "INACTIVE",
                "expectedVersion": source["version"],
                "reason": "异常重排公共 API 验收：临时设备故障",
            },
        )
        source_disabled = True
        require(disabled.get("status") == "INACTIVE", "room was not disabled")

        duplicate_status, duplicate = request_raw(
            "PATCH",
            f"{public_base}/api/v1/admin/rooms/{source['id']}/status",
            headers=admin_headers,
            body={
                "status": "INACTIVE",
                "expectedVersion": source["version"],
                "reason": "重复停用不得重复建单",
            },
        )
        require(
            duplicate_status == 409 and duplicate.get("code") == "ROOM_STATE_CONFLICT",
            "stale room status update was not rejected",
        )

        query = urlencode({"status": "OPEN", "page": 1, "size": 100})
        cases = request_json(
            "GET", f"{public_base}/api/v1/replan-cases?{query}", headers=organizer_headers
        ).get("items")
        require(isinstance(cases, list), "replan case list has no items")
        matching = [case for case in cases if case.get("meetingId") == meeting_id]
        require(len(matching) == 1, "room failure did not create exactly one open case")
        case = matching[0]
        case_id = case.get("id")
        require(isinstance(case_id, int), "replan case has no id")

        foreign_status, foreign = request_raw(
            "GET",
            f"{public_base}/api/v1/replan-cases/{case_id}",
            headers=participant_headers,
        )
        require(
            foreign_status == 404 and foreign.get("code") == "REPLAN_CASE_NOT_FOUND",
            "a participant could read the organizer's replan case",
        )

        notifications_query = urlencode({"unreadOnly": "true", "page": 1, "size": 100})
        organizer_notifications = request_json(
            "GET",
            f"{public_base}/api/v1/notifications?{notifications_query}",
            headers=organizer_headers,
        ).get("items")
        require(isinstance(organizer_notifications, list), "organizer inbox has no items")
        resource_notifications = [
            item
            for item in organizer_notifications
            if item.get("type") == "RESOURCE_UNAVAILABLE"
            and item.get("relatedReplanCaseId") == case_id
        ]
        require(len(resource_notifications) == 1, "organizer resource notification is not unique")
        participant_notifications = request_json(
            "GET",
            f"{public_base}/api/v1/notifications?{notifications_query}",
            headers=participant_headers,
        ).get("items")
        require(isinstance(participant_notifications, list), "participant inbox has no items")
        require(
            not any(
                item.get("type") == "RESOURCE_UNAVAILABLE"
                and item.get("relatedMeetingId") == meeting_id
                for item in participant_notifications
            ),
            "resource failure notification leaked to a participant",
        )

        alternatives = request_json(
            "GET",
            f"{public_base}/api/v1/replan-cases/{case_id}/alternatives?limit=3",
            headers=organizer_headers,
        )
        items = alternatives.get("items")
        require(isinstance(items, list) and items, "no quick replacement candidate was returned")
        require(alternatives.get("sameTime") is True, "quick alternatives changed the time")
        candidate = next(
            (item for item in items if item.get("roomId") == expected_alternative["id"]),
            items[0],
        )

        stale_status, stale = request_raw(
            "POST",
            f"{public_base}/api/v1/replan-cases/{case_id}/resolve",
            headers=organizer_headers,
            body={
                "roomId": candidate["roomId"],
                "expectedMeetingVersion": alternatives["meetingVersion"],
                "expectedCaseVersion": alternatives["caseVersion"] + 1,
            },
        )
        require(
            stale_status == 409 and stale.get("code") == "REPLAN_CASE_STATE_CONFLICT",
            "stale case version was not rejected",
        )

        resolved = request_json(
            "POST",
            f"{public_base}/api/v1/replan-cases/{case_id}/resolve",
            headers=organizer_headers,
            body={
                "roomId": candidate["roomId"],
                "expectedMeetingVersion": alternatives["meetingVersion"],
                "expectedCaseVersion": alternatives["caseVersion"],
            },
        )
        require(resolved.get("status") == "RESOLVED", "case did not resolve")
        require(
            resolved.get("currentMeeting", {}).get("roomId") == candidate["roomId"],
            "resolved meeting did not use the replacement room",
        )
    finally:
        if source_disabled:
            with suppress(OSError, SmokeFailure):
                current_room = request_json(
                    "GET", f"{public_base}/api/v1/rooms/{source['id']}", headers=admin_headers
                )
                if current_room.get("status") == "INACTIVE":
                    request_json(
                        "PATCH",
                        f"{public_base}/api/v1/admin/rooms/{source['id']}/status",
                        headers=admin_headers,
                        body={
                            "status": "ACTIVE",
                            "expectedVersion": current_room["version"],
                        },
                    )
        if meeting_id is not None:
            with suppress(OSError, SmokeFailure):
                request_json(
                    "DELETE",
                    f"{public_base}/api/v1/meetings/{meeting_id}",
                    headers=organizer_headers,
                )

    print(
        json.dumps(
            {
                "roomFailure": "case-and-organizer-notification-PASS",
                "caseIsolation": "PASS",
                "quickAlternatives": "same-time-hard-constraints-PASS",
                "doubleVersion": "PASS",
                "resolution": "meeting-and-case-PASS",
                "cleanup": "room-restored-meeting-cancelled",
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
        print(f"Exception replan smoke failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
