#!/usr/bin/env python3
"""Public-API smoke for ADMIN employee management and per-user notifications.

The script only calls Java's browser-facing `/api/v1/**` surface. It reuses one
stable fictional employee account, leaves it DISABLED, and cancels every meeting
it creates. No database, internal Tool, Python, or destructive volume access is
used.
"""

from __future__ import annotations

import argparse
import json
import secrets
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


def login(public_base: str, username: str, password: str) -> dict[str, str]:
    data = request_json(
        "POST",
        f"{public_base}/api/v1/auth/login",
        body={"username": username, "password": password},
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
    for day_offset in range(7):
        start = first_day + timedelta(days=day_offset)
        query = urlencode({"from": start.isoformat(), "to": start.replace(hour=18).isoformat()})
        for room in rooms:
            if not isinstance(room, dict) or not isinstance(room.get("id"), int):
                continue
            room_id = room["id"]
            slots = request_json(
                "GET",
                f"{public_base}/api/v1/rooms/{room_id}/availability?{query}",
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
                    return room_id, left["startAt"], right["endAt"]
    raise SmokeFailure("no two consecutive public availability slots were found")


def employee_by_username(
    public_base: str, admin_headers: dict[str, str], username: str
) -> dict[str, Any] | None:
    query = urlencode({"keyword": username, "page": 1, "size": 100})
    items = request_json(
        "GET", f"{public_base}/api/v1/admin/employees?{query}", headers=admin_headers
    ).get("items")
    require(isinstance(items, list), "employee list response has no items")
    exact = [item for item in items if isinstance(item, dict) and item.get("username") == username]
    require(len(exact) <= 1, "employee username is not unique")
    return exact[0] if exact else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-base", default="http://localhost")
    args = parser.parse_args()
    public_base = args.public_base.rstrip("/")

    admin_headers = login(public_base, "admin", "demo-password")
    organizer_headers = login(public_base, "zhangsan", "demo-password")
    departments = request_json(
        "GET", f"{public_base}/api/v1/admin/departments", headers=admin_headers
    ).get("items")
    require(isinstance(departments, list) and departments, "no active department option exists")
    department_id = departments[0].get("id") if isinstance(departments[0], dict) else None
    require(isinstance(department_id, int), "department option has no id")

    username = "smoke.notify"
    email = "smoke.notify@example.com"
    stable_password = "Smoke-stable-2026!"
    current = employee_by_username(public_base, admin_headers, username)
    if current is None:
        current = request_json(
            "POST",
            f"{public_base}/api/v1/admin/employees",
            headers=admin_headers,
            body={
                "username": username,
                "initialPassword": stable_password,
                "displayName": "通知验收员工",
                "email": email,
                "departmentId": department_id,
                "role": "EMPLOYEE",
                "status": "ACTIVE",
            },
        )

    employee_id = current.get("id")
    require(isinstance(employee_id, int), "managed employee has no id")
    meeting_id: int | None = None
    active_password = f"Smoke-{secrets.token_urlsafe(12)}!"
    try:
        current = request_json(
            "PUT",
            f"{public_base}/api/v1/admin/employees/{employee_id}",
            headers=admin_headers,
            body={
                "displayName": "通知验收员工",
                "email": email,
                "departmentId": department_id,
                "role": "EMPLOYEE",
                "expectedVersion": current["version"],
            },
        )
        current = request_json(
            "POST",
            f"{public_base}/api/v1/admin/employees/{employee_id}/password",
            headers=admin_headers,
            body={"newPassword": active_password, "expectedVersion": current["version"]},
        )
        if current.get("status") != "ACTIVE":
            current = request_json(
                "PATCH",
                f"{public_base}/api/v1/admin/employees/{employee_id}/status",
                headers=admin_headers,
                body={"status": "ACTIVE", "expectedVersion": current["version"]},
            )

        managed_headers = login(public_base, username, active_password)
        forbidden_status, forbidden = request_raw(
            "GET", f"{public_base}/api/v1/admin/employees", headers=managed_headers
        )
        require(
            forbidden_status == 403 and forbidden.get("code") == "FORBIDDEN",
            "employee management RBAC is not enforced",
        )

        request_json(
            "PATCH",
            f"{public_base}/api/v1/notifications/read-all",
            headers=managed_headers,
        )
        room_id, start_at, end_at = find_available_interval(public_base, organizer_headers)
        meeting = request_json(
            "POST",
            f"{public_base}/api/v1/meetings",
            headers={
                **organizer_headers,
                "Idempotency-Key": f"idem-notification-smoke-{uuid.uuid4().hex}",
            },
            body={
                "title": f"站内通知验收 {uuid.uuid4().hex[:8]}",
                "meetingType": "GENERAL",
                "roomId": room_id,
                "startAt": start_at,
                "endAt": end_at,
                "requiredParticipantIds": [employee_id],
                "optionalParticipantIds": [],
            },
        )
        meeting_id = meeting.get("id")
        require(isinstance(meeting_id, int), "created meeting has no id")

        updated = request_json(
            "PUT",
            f"{public_base}/api/v1/meetings/{meeting_id}",
            headers=organizer_headers,
            body={
                "title": f"{meeting['title']}（已变更）",
                "meetingType": meeting["meetingType"],
                "roomId": meeting["roomId"],
                "startAt": meeting["startAt"],
                "endAt": meeting["endAt"],
                "requiredParticipantIds": [employee_id],
                "optionalParticipantIds": [],
                "expectedVersion": meeting["version"],
            },
        )
        require(updated.get("version") == meeting["version"] + 1, "meeting version did not advance")
        cancelled = request_json(
            "DELETE", f"{public_base}/api/v1/meetings/{meeting_id}", headers=organizer_headers
        )
        require(cancelled.get("status") == "CANCELLED", "meeting was not cancelled")
        meeting_id = None

        query = urlencode({"unreadOnly": "true", "page": 1, "size": 100})
        inbox = request_json(
            "GET", f"{public_base}/api/v1/notifications?{query}", headers=managed_headers
        )
        items = inbox.get("items")
        require(isinstance(items, list), "notification list has no items")
        related = [
            item
            for item in items
            if isinstance(item, dict) and item.get("relatedMeetingId") == cancelled["id"]
        ]
        types = {item.get("type") for item in related}
        require(
            types == {"MEETING_CONFIRMED", "MEETING_CHANGED", "MEETING_CANCELLED"},
            f"meeting notifications are incomplete: {sorted(str(value) for value in types)}",
        )
        require(inbox.get("unreadCount", 0) >= 3, "unread count did not include new notifications")

        first_id = related[0].get("id")
        require(isinstance(first_id, int), "notification has no id")
        foreign_status, foreign_response = request_raw(
            "PATCH",
            f"{public_base}/api/v1/notifications/{first_id}/read",
            headers=admin_headers,
        )
        require(
            foreign_status == 404 and foreign_response.get("code") == "NOTIFICATION_NOT_FOUND",
            "an administrator could update another user's notification",
        )
        marked = request_json(
            "PATCH",
            f"{public_base}/api/v1/notifications/{first_id}/read",
            headers=managed_headers,
        )
        require(marked.get("readAt") is not None, "single notification was not marked read")
        marked_again = request_json(
            "PATCH",
            f"{public_base}/api/v1/notifications/{first_id}/read",
            headers=managed_headers,
        )
        require(marked_again.get("readAt") == marked.get("readAt"), "single read is not idempotent")
        request_json(
            "PATCH",
            f"{public_base}/api/v1/notifications/read-all",
            headers=managed_headers,
        )
        unread = request_json(
            "GET", f"{public_base}/api/v1/notifications/unread-count", headers=managed_headers
        )
        require(unread.get("unreadCount") == 0, "read-all did not clear the unread count")
    finally:
        if meeting_id is not None:
            with suppress(OSError, SmokeFailure):
                request_json(
                    "DELETE",
                    f"{public_base}/api/v1/meetings/{meeting_id}",
                    headers=organizer_headers,
                )
        latest = employee_by_username(public_base, admin_headers, username)
        if latest is not None:
            try:
                latest = request_json(
                    "POST",
                    f"{public_base}/api/v1/admin/employees/{employee_id}/password",
                    headers=admin_headers,
                    body={"newPassword": stable_password, "expectedVersion": latest["version"]},
                )
                if latest.get("status") != "DISABLED":
                    request_json(
                        "PATCH",
                        f"{public_base}/api/v1/admin/employees/{employee_id}/status",
                        headers=admin_headers,
                        body={"status": "DISABLED", "expectedVersion": latest["version"]},
                    )
            except (OSError, SmokeFailure):
                pass

    print(
        json.dumps(
            {
                "employeeManagement": "created-or-reused-edited-reset-enabled-disabled",
                "employeeRbac": "PASS",
                "meetingNotifications": [
                    "MEETING_CONFIRMED",
                    "MEETING_CHANGED",
                    "MEETING_CANCELLED",
                ],
                "notificationReadIsolation": "single-and-read-all-PASS",
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
        print(f"Employee/notification smoke failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
