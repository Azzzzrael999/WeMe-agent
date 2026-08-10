#!/usr/bin/env python3
"""Public-API smoke for the real pre/post meeting closure.

The script talks only to Java's `/api/v1/**` surface. It creates and cancels one
future fictional meeting for preparation checks, then uses the seeded completed
demo meeting for Agent draft -> EDIT -> ACCEPT -> action-item state coverage.
It never accesses databases, internal services, Docker volumes, or secrets.
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
        with urlopen(request, timeout=90) as response:
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


def find_available_interval(
    public_base: str, headers: dict[str, str]
) -> tuple[int, str, str]:
    rooms = request_json("GET", f"{public_base}/api/v1/rooms", headers=headers).get("items")
    require(isinstance(rooms, list) and rooms, "no active rooms are available")
    zone = ZoneInfo("Asia/Shanghai")
    first_day = (datetime.now(zone) + timedelta(days=8)).replace(
        hour=9, minute=0, second=0, microsecond=0
    )
    for day_offset in range(6):
        day = first_day + timedelta(days=day_offset)
        query = urlencode({"from": day.isoformat(), "to": day.replace(hour=18).isoformat()})
        for room in rooms:
            if (
                not isinstance(room, dict)
                or not isinstance(room.get("id"), int)
                or not isinstance(room.get("capacity"), int)
                or room["capacity"] < 2
                or room.get("isHot") is True
            ):
                continue
            slots = request_json(
                "GET",
                f"{public_base}/api/v1/rooms/{room['id']}/availability?{query}",
                headers=headers,
            ).get("availableSlots")
            if not isinstance(slots, list):
                continue
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
                    return room["id"], left["startAt"], right["endAt"]
    raise SmokeFailure("no one-hour interval is available for the preparation smoke")


def exercise_preparation(
    public_base: str,
    organizer_headers: dict[str, str],
    participant_headers: dict[str, str],
) -> int:
    room_id, start_at, end_at = find_available_interval(public_base, organizer_headers)
    create_headers = {
        **organizer_headers,
        "Idempotency-Key": f"smoke-preparation-{uuid.uuid4().hex}",
    }
    meeting = request_json(
        "POST",
        f"{public_base}/api/v1/meetings",
        headers=create_headers,
        body={
            "title": "支付网关 V2 会前闭环 Smoke",
            "meetingType": "ARCHITECTURE_REVIEW",
            "roomId": room_id,
            "startAt": start_at,
            "endAt": end_at,
            "requiredParticipantIds": [1001, 1003],
            "optionalParticipantIds": [],
        },
    )
    meeting_id = meeting.get("id")
    require(isinstance(meeting_id, int), "meeting create returned no ID")
    lifecycle_url = f"{public_base}/api/v1/meetings/{meeting_id}/lifecycle"
    lifecycle = request_json("GET", lifecycle_url, headers=organizer_headers)
    require(lifecycle.get("preparation", {}).get("version") == 0, "initial version is not zero")

    missing = request_json(
        "PUT",
        f"{public_base}/api/v1/meetings/{meeting_id}/preparation",
        headers=organizer_headers,
        body={
            "expectedVersion": 0,
            "agendaItems": [
                {
                    "topic": "确认发布范围",
                    "ownerEmployeeId": 1001,
                    "plannedMinutes": 30,
                }
            ],
            "materials": [
                {
                    "title": "上线方案 V3",
                    "ownerEmployeeId": 1003,
                    "required": True,
                    "status": "MISSING",
                    "versionLabel": "v3",
                    "note": "等待最终时序图",
                }
            ],
        },
    )
    require(
        missing.get("preparation", {}).get("checklist", {}).get("status")
        == "NEEDS_ATTENTION",
        "missing material did not fail the checklist",
    )
    ready = request_json(
        "PUT",
        f"{public_base}/api/v1/meetings/{meeting_id}/preparation",
        headers=organizer_headers,
        body={
            "expectedVersion": 1,
            "agendaItems": [
                {
                    "topic": "确认发布范围",
                    "ownerEmployeeId": 1001,
                    "plannedMinutes": 30,
                }
            ],
            "materials": [
                {
                    "title": "上线方案 V3",
                    "ownerEmployeeId": 1003,
                    "required": True,
                    "status": "READY",
                    "versionLabel": "v3",
                    "note": "时序图已补齐",
                }
            ],
        },
    )
    require(
        ready.get("preparation", {}).get("checklist", {}).get("status") == "READY",
        "ready preparation did not pass the checklist",
    )

    forbidden_status, forbidden = request_raw(
        "PUT",
        f"{public_base}/api/v1/meetings/{meeting_id}/preparation",
        headers=participant_headers,
        body={"expectedVersion": 2, "agendaItems": [], "materials": []},
    )
    require(forbidden_status == 403 and forbidden.get("code") == "FORBIDDEN", "participant write was not forbidden")

    stale_status, stale = request_raw(
        "PUT",
        f"{public_base}/api/v1/meetings/{meeting_id}/preparation",
        headers=organizer_headers,
        body={"expectedVersion": 1, "agendaItems": [], "materials": []},
    )
    require(
        stale_status == 409 and stale.get("code") == "MEETING_CONTENT_STATE_CONFLICT",
        "stale preparation version was not rejected",
    )
    return meeting_id


def find_completed_demo(public_base: str, headers: dict[str, str]) -> int:
    query = urlencode({"status": "COMPLETED", "page": 1, "size": 100})
    items = request_json(
        "GET", f"{public_base}/api/v1/meetings?{query}", headers=headers
    ).get("items")
    require(isinstance(items, list), "completed meeting query returned no items")
    meeting = next(
        (
            item
            for item in items
            if isinstance(item, dict)
            and item.get("meetingNo") == "MTG-DEMO-POST-20260813"
        ),
        None,
    )
    require(isinstance(meeting, dict) and isinstance(meeting.get("id"), int), "completed demo meeting is missing")
    return meeting["id"]


def exercise_post_meeting(
    public_base: str,
    organizer_headers: dict[str, str],
    participant_headers: dict[str, str],
) -> tuple[int, int | None]:
    meeting_id = find_completed_demo(public_base, organizer_headers)
    lifecycle_url = f"{public_base}/api/v1/meetings/{meeting_id}/lifecycle"
    lifecycle = request_json("GET", lifecycle_url, headers=organizer_headers)
    post = lifecycle.get("postMeeting")
    require(isinstance(post, dict), "completed meeting has no postMeeting view")
    if post.get("minutes") is None:
        draft = post.get("draft")
        if not isinstance(draft, dict) or draft.get("status") not in {
            "PENDING_REVIEW",
            "PROCESSING",
        }:
            lifecycle = request_json(
                "POST",
                f"{public_base}/api/v1/meetings/{meeting_id}/post-meeting-drafts",
                headers={
                    **organizer_headers,
                    "Idempotency-Key": f"smoke-post-{uuid.uuid4().hex}",
                },
                body={
                    "transcript": (
                        "决定采用灰度发布并保留人工回滚开关。"
                        "李四负责补充回滚演练，截止 2026-08-20 18:00。"
                    )
                },
            )
            draft = lifecycle.get("postMeeting", {}).get("draft")
        require(isinstance(draft, dict), "Agent draft was not created")
        require(draft.get("status") == "PENDING_REVIEW", "Agent draft is not reviewable")
        draft_id = draft.get("id")
        draft_version = draft.get("version")
        require(isinstance(draft_id, int) and isinstance(draft_version, int), "draft identity is invalid")

        forbidden_status, forbidden = request_raw(
            "POST",
            f"{public_base}/api/v1/meetings/{meeting_id}/post-meeting-drafts/{draft_id}/review",
            headers=participant_headers,
            body={"action": "REJECT", "expectedVersion": draft_version, "editedDraft": None},
        )
        require(forbidden_status == 403 and forbidden.get("code") == "FORBIDDEN", "participant review was not forbidden")

        due_at = (datetime.now(ZoneInfo("Asia/Shanghai")) + timedelta(days=2)).replace(
            hour=18, minute=0, second=0, microsecond=0
        ).isoformat()
        edited = request_json(
            "POST",
            f"{public_base}/api/v1/meetings/{meeting_id}/post-meeting-drafts/{draft_id}/review",
            headers=organizer_headers,
            body={
                "action": "EDIT",
                "expectedVersion": draft_version,
                "editedDraft": {
                    "minutes": {
                        "background": "支付网关 V2 上线前复盘。",
                        "discussionSummary": "团队核对了灰度发布、监控与回滚演练。",
                        "conclusion": "保留人工回滚开关，完成演练后上线。",
                    },
                    "decisions": [
                        {"content": "采用灰度发布。", "rationale": "降低一次性发布风险。"}
                    ],
                    "actionItems": [
                        {
                            "title": "补充回滚演练",
                            "description": "完成演练并记录结果。",
                            "assigneeEmployeeId": 1003,
                            "dueAt": due_at,
                        }
                    ],
                },
            },
        )
        edited_post = edited.get("postMeeting", {})
        require(edited_post.get("minutes") is None, "EDIT wrote formal minutes")
        edited_draft = edited_post.get("draft")
        require(
            isinstance(edited_draft, dict)
            and edited_draft.get("status") == "PENDING_REVIEW"
            and edited_draft.get("version") == draft_version + 1,
            "EDIT did not keep a new pending draft version",
        )
        accepted = request_json(
            "POST",
            f"{public_base}/api/v1/meetings/{meeting_id}/post-meeting-drafts/{draft_id}/review",
            headers=organizer_headers,
            body={
                "action": "ACCEPT",
                "expectedVersion": draft_version + 1,
                "editedDraft": None,
            },
        )
        post = accepted.get("postMeeting")
    require(isinstance(post, dict) and isinstance(post.get("minutes"), dict), "formal minutes are missing")
    decisions = post.get("decisions")
    actions = post.get("actionItems")
    require(isinstance(decisions, list) and decisions, "formal decisions are missing")
    require(isinstance(actions, list) and actions, "formal action items are missing")
    action = actions[0]
    require(isinstance(action, dict) and isinstance(action.get("id"), int), "action item is invalid")
    action_id = action["id"]
    if action.get("status") != "DONE":
        updated = request_json(
            "PATCH",
            f"{public_base}/api/v1/meetings/{meeting_id}/action-items/{action_id}",
            headers=participant_headers,
            body={"status": "DONE", "expectedVersion": action.get("version")},
        )
        require(updated.get("status") == "DONE", "assignee could not complete action item")
    return meeting_id, action_id


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-base", default="http://localhost")
    args = parser.parse_args()
    public_base = args.public_base.rstrip("/")
    organizer_headers = login(public_base, "zhangsan")
    participant_headers = login(public_base, "lisi")
    created_meeting_id: int | None = None
    try:
        created_meeting_id = exercise_preparation(
            public_base, organizer_headers, participant_headers
        )
        completed_meeting_id, action_item_id = exercise_post_meeting(
            public_base, organizer_headers, participant_headers
        )
        print(
            json.dumps(
                {
                    "prePostMeetingClosure": "PASS",
                    "preparationMeetingId": created_meeting_id,
                    "completedDemoMeetingId": completed_meeting_id,
                    "actionItemId": action_item_id,
                },
                ensure_ascii=False,
            )
        )
        return 0
    except SmokeFailure as exc:
        print(f"pre/post meeting smoke failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if created_meeting_id is not None:
            with suppress(Exception):
                request_raw(
                    "DELETE",
                    f"{public_base}/api/v1/meetings/{created_meeting_id}",
                    headers=organizer_headers,
                )


if __name__ == "__main__":
    raise SystemExit(main())
