#!/usr/bin/env python3
"""Day 3 real-stack smoke test for Tool auth, HOT booking and MQ completion."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

SHANGHAI = timezone(timedelta(hours=8))


class ApiFailure(RuntimeError):
    def __init__(self, status: int, body: dict[str, Any]) -> None:
        super().__init__(f"HTTP {status}: {body.get('code', body)}")
        self.status = status
        self.body = body


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name.strip()] = value.strip()
    return values


def b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def agent_context_token(
    secret: str,
    user_id: int,
    trace_id: str,
    run_id: str,
    audience: str = "agent-service",
) -> str:
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": str(user_id),
        "roles": ["EMPLOYEE"],
        "traceId": trace_id,
        "runId": run_id,
        "aud": audience,
        "iat": now,
        "exp": now + 600,
    }
    signing_input = ".".join(
        b64url(json.dumps(part, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        for part in (header, payload)
    )
    signature = hmac.new(secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256)
    return f"{signing_input}.{b64url(signature.digest())}"


def request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    timeout: float = 20,
) -> tuple[int, dict[str, Any]]:
    data = None
    actual_headers = dict(headers or {})
    if body is not None:
        data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        actual_headers["Content-Type"] = "application/json; charset=utf-8"
    req = Request(url, data=data, headers=actual_headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return response.status, payload
    except HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"code": "NON_JSON_ERROR", "message": raw}
        raise ApiFailure(exc.code, payload) from exc


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-base", default="http://localhost")
    parser.add_argument("--business-base", default="http://localhost:18080")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--poll-seconds", type=int, default=45)
    args = parser.parse_args()

    public_base = args.public_base.rstrip("/")
    business_base = args.business_base.rstrip("/")
    env = parse_env(args.env_file)
    service_token = env.get("INTERNAL_SERVICE_TOKEN", "")
    context_secret = env.get("AGENT_CONTEXT_JWT_SECRET", "")
    require(bool(service_token), "INTERNAL_SERVICE_TOKEN is missing")
    require(bool(context_secret), "AGENT_CONTEXT_JWT_SECRET is missing")

    _, login = request_json(
        "POST",
        f"{public_base}/api/v1/auth/login",
        body={"username": "zhangsan", "password": "demo-password"},
    )
    access_token = login["data"]["accessToken"]
    user_id = int(login["data"]["user"]["id"])
    user_headers = {"Authorization": f"Bearer {access_token}"}

    try:
        request_json(
            "POST",
            f"{public_base}/api/v1/agent/runs/stream",
            headers={**user_headers, "Accept": "text/event-stream"},
            body={
                "threadId": None,
                "message": "Day 3 SSE boundary probe",
                "clientRequestId": str(uuid.uuid4()),
            },
        )
        raise AssertionError("SSE proxy unexpectedly fabricated a response without a Python endpoint")
    except ApiFailure as exc:
        require(exc.status == 503, f"SSE unavailable path returned HTTP {exc.status}")
        require(exc.body.get("code") == "AGENT_UNAVAILABLE", "wrong SSE unavailable error code")

    trace_id = f"trc_{uuid.uuid4().hex}"
    run_id = f"run_{uuid.uuid4().hex}"
    context_token = agent_context_token(context_secret, user_id, trace_id, run_id)

    def tool_headers(tool_call_id: str | None = None) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {context_token}",
            "X-Service-Token": service_token,
            "X-Trace-Id": trace_id,
            "X-Run-Id": run_id,
            "X-Tool-Call-Id": tool_call_id or f"tool_{uuid.uuid4().hex}",
        }

    missing_service_headers = tool_headers()
    missing_service_headers.pop("X-Service-Token")
    try:
        request_json(
            "POST",
            f"{business_base}/internal/v1/tools/resolve-employees",
            headers=missing_service_headers,
            body={"names": ["张三"], "departmentNames": []},
        )
        raise AssertionError("Tool request without service token unexpectedly succeeded")
    except ApiFailure as exc:
        require(exc.status == 401, f"missing service token returned HTTP {exc.status}")
        require(exc.body.get("code") == "SERVICE_TOKEN_INVALID", "wrong service-token error code")

    bad_audience_headers = tool_headers()
    bad_audience_headers["Authorization"] = (
        "Bearer "
        + agent_context_token(context_secret, user_id, trace_id, run_id, "wrong-audience")
    )
    try:
        request_json(
            "POST",
            f"{business_base}/internal/v1/tools/resolve-employees",
            headers=bad_audience_headers,
            body={"names": ["张三"], "departmentNames": []},
        )
        raise AssertionError("Tool request with a bad audience unexpectedly succeeded")
    except ApiFailure as exc:
        require(exc.status == 401, f"bad audience returned HTTP {exc.status}")
        require(exc.body.get("code") == "AGENT_CONTEXT_INVALID", "wrong context-token error code")

    try:
        request_json(
            "POST",
            f"{business_base}/internal/v1/tools/get-employee-free-busy",
            headers=tool_headers(),
            body={
                "employeeIds": list(range(1, 52)),
                "from": (datetime.now(SHANGHAI) + timedelta(days=1)).isoformat(timespec="seconds"),
                "to": (datetime.now(SHANGHAI) + timedelta(days=1, hours=1)).isoformat(timespec="seconds"),
            },
        )
        raise AssertionError("Tool request above the 50-employee cap unexpectedly succeeded")
    except ApiFailure as exc:
        require(exc.status == 400, f"employee cap returned HTTP {exc.status}")
        require(exc.body.get("code") == "VALIDATION_ERROR", "wrong employee-cap error code")

    _, resolved = request_json(
        "POST",
        f"{business_base}/internal/v1/tools/resolve-employees",
        headers=tool_headers(),
        body={"names": ["张三"], "departmentNames": []},
    )
    require(
        any(employee.get("employeeId") == user_id for employee in resolved["data"]["employees"]),
        "resolve-employees did not return the authenticated demo employee",
    )

    local_now = datetime.now(SHANGHAI)
    target_day = (local_now + timedelta(days=12)).date()
    start_at = datetime.combine(target_day, datetime.min.time(), SHANGHAI).replace(hour=9)
    end_at = start_at + timedelta(minutes=90)
    start_text = start_at.isoformat(timespec="seconds")
    end_text = end_at.isoformat(timespec="seconds")

    _, room_result = request_json(
        "POST",
        f"{business_base}/internal/v1/tools/search-available-rooms",
        headers=tool_headers(),
        body={
            "from": start_text,
            "to": end_text,
            "minimumCapacity": 2,
            "requiredFeatures": ["LARGE_SCREEN"],
            "limit": 50,
        },
    )
    require(any(room.get("roomId") == 103 for room in room_result["data"]["rooms"]), "HOT room 103 unavailable")

    _, free_busy = request_json(
        "POST",
        f"{business_base}/internal/v1/tools/get-employee-free-busy",
        headers=tool_headers(),
        body={"employeeIds": [user_id], "from": start_text, "to": end_text},
    )
    require(len(free_busy["data"]["employees"]) == 1, "free-busy response shape is invalid")

    draft_request = {
        "title": "Day 3 HOT MQ smoke",
        "meetingType": "ARCHITECTURE_REVIEW",
        "roomId": 103,
        "startAt": start_text,
        "endAt": end_text,
        "requiredParticipantIds": [1002],
        "optionalParticipantIds": [],
    }
    _, draft = request_json(
        "POST",
        f"{business_base}/internal/v1/tools/booking-drafts",
        headers=tool_headers(),
        body=draft_request,
    )
    confirmation_token = draft["data"]["confirmationToken"]
    require(bool(confirmation_token), "draft did not return confirmationToken")

    confirm_tool_call_id = f"tool_{uuid.uuid4().hex}"
    confirm_idempotency_key = str(uuid.uuid4())
    confirm_headers = tool_headers(confirm_tool_call_id)
    confirm_headers["Idempotency-Key"] = confirm_idempotency_key
    confirm_url = (
        f"{business_base}/internal/v1/tools/booking-drafts/"
        f"{quote(confirmation_token, safe='')}/confirm"
    )
    confirm_status, confirmed = request_json("POST", confirm_url, headers=confirm_headers)
    require(confirm_status == 202, f"HOT confirm returned HTTP {confirm_status}, expected 202")
    require(confirmed["data"]["status"] == "PENDING", "HOT confirm did not return PENDING")
    request_no = confirmed["data"]["requestNo"]
    require(bool(request_no), "HOT confirm did not return requestNo")

    replay_status, replay = request_json("POST", confirm_url, headers=confirm_headers)
    require(replay_status == 202, f"Tool replay returned HTTP {replay_status}")
    require(replay["data"]["requestNo"] == request_no, "Tool replay changed requestNo")

    deadline = time.monotonic() + args.poll_seconds
    booking: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        _, response = request_json(
            "GET",
            f"{public_base}/api/v1/booking-requests/{quote(request_no, safe='')}",
            headers=user_headers,
        )
        booking = response["data"]
        if booking["status"] in {"SUCCESS", "CONFLICT", "FAILED"}:
            break
        time.sleep(0.5)
    require(booking is not None, "booking request was never readable")
    require(booking["status"] == "SUCCESS", f"booking finished as {booking['status']}")
    meeting_id = int(booking["meetingId"])

    _, meeting = request_json(
        "GET",
        f"{public_base}/api/v1/meetings/{meeting_id}",
        headers=user_headers,
    )
    require(meeting["data"]["source"] == "AGENT", "MQ-created meeting source is not AGENT")
    require(meeting["data"]["status"] == "CONFIRMED", "MQ-created meeting is not CONFIRMED")

    _, recent = request_json(
        "POST",
        f"{business_base}/internal/v1/tools/get-recent-meeting",
        headers=tool_headers(),
        body={"limit": 5},
    )
    require(
        any(item.get("id") == meeting_id for item in recent["data"]["meetings"]),
        "get-recent-meeting did not include the MQ-created meeting",
    )

    _, cancelled = request_json(
        "DELETE",
        f"{public_base}/api/v1/meetings/{meeting_id}",
        headers=user_headers,
    )
    require(cancelled["data"]["status"] == "CANCELLED", "cleanup cancellation failed")

    conflict_start = start_at.replace(hour=14, minute=0)
    conflict_end = conflict_start + timedelta(minutes=60)
    conflict_start_text = conflict_start.isoformat(timespec="seconds")
    conflict_end_text = conflict_end.isoformat(timespec="seconds")
    blocker_headers = dict(user_headers)
    blocker_headers["Idempotency-Key"] = str(uuid.uuid4())
    _, blocker = request_json(
        "POST",
        f"{public_base}/api/v1/meetings",
        headers=blocker_headers,
        body={
            "title": "Day 3 MQ conflict blocker",
            "meetingType": "TECH_REVIEW",
            "roomId": 103,
            "startAt": conflict_start_text,
            "endAt": conflict_end_text,
            "requiredParticipantIds": [],
            "optionalParticipantIds": [],
        },
    )
    blocker_id = int(blocker["data"]["id"])

    try:
        _, conflict_draft = request_json(
            "POST",
            f"{business_base}/internal/v1/tools/booking-drafts",
            headers=tool_headers(),
            body={
                "title": "Day 3 MQ expected conflict",
                "meetingType": "TECH_REVIEW",
                "roomId": 103,
                "startAt": conflict_start_text,
                "endAt": conflict_end_text,
                "requiredParticipantIds": [],
                "optionalParticipantIds": [],
            },
        )
        conflict_token = conflict_draft["data"]["confirmationToken"]
        conflict_confirm_headers = tool_headers()
        conflict_confirm_headers["Idempotency-Key"] = str(uuid.uuid4())
        conflict_status, conflict_pending = request_json(
            "POST",
            f"{business_base}/internal/v1/tools/booking-drafts/{quote(conflict_token, safe='')}/confirm",
            headers=conflict_confirm_headers,
        )
        require(conflict_status == 202, "conflicting HOT confirmation did not return HTTP 202")
        conflict_request_no = conflict_pending["data"]["requestNo"]

        conflict_deadline = time.monotonic() + args.poll_seconds
        conflict_booking: dict[str, Any] | None = None
        while time.monotonic() < conflict_deadline:
            _, conflict_response = request_json(
                "GET",
                f"{public_base}/api/v1/booking-requests/{quote(conflict_request_no, safe='')}",
                headers=user_headers,
            )
            conflict_booking = conflict_response["data"]
            if conflict_booking["status"] in {"SUCCESS", "CONFLICT", "FAILED"}:
                break
            time.sleep(0.5)
        require(conflict_booking is not None, "conflicting booking request was never readable")
        require(
            conflict_booking["status"] == "CONFLICT",
            f"conflicting booking finished as {conflict_booking['status']}",
        )
        require(conflict_booking.get("meetingId") is None, "conflicting booking created a meeting")
    finally:
        request_json(
            "DELETE",
            f"{public_base}/api/v1/meetings/{blocker_id}",
            headers=user_headers,
        )

    draft_only_title = f"Day 3 no side effect {uuid.uuid4().hex[:8]}"
    _, draft_only = request_json(
        "POST",
        f"{business_base}/internal/v1/tools/booking-drafts",
        headers=tool_headers(),
        body={
            "title": draft_only_title,
            "meetingType": "TECH_REVIEW",
            "roomId": 101,
            "startAt": start_at.replace(hour=18, minute=0).isoformat(timespec="seconds"),
            "endAt": start_at.replace(hour=19, minute=0).isoformat(timespec="seconds"),
            "requiredParticipantIds": [],
            "optionalParticipantIds": [],
        },
    )
    require(draft_only["data"].get("status", "PENDING") == "PENDING", "draft-only status is not PENDING")

    managed_start = start_at.replace(hour=16, minute=0)
    managed_end = managed_start + timedelta(minutes=60)
    managed_headers = dict(user_headers)
    managed_headers["Idempotency-Key"] = str(uuid.uuid4())
    _, managed = request_json(
        "POST",
        f"{public_base}/api/v1/meetings",
        headers=managed_headers,
        body={
            "title": "Day 3 draft-managed meeting",
            "meetingType": "TECH_REVIEW",
            "roomId": 101,
            "startAt": managed_start.isoformat(timespec="seconds"),
            "endAt": managed_end.isoformat(timespec="seconds"),
            "requiredParticipantIds": [],
            "optionalParticipantIds": [],
        },
    )
    managed_id = int(managed["data"]["id"])
    managed_version = int(managed["data"]["version"])

    proposed_start = managed_start.replace(hour=17)
    proposed_end = proposed_start + timedelta(minutes=60)
    _, reschedule_draft = request_json(
        "POST",
        f"{business_base}/internal/v1/tools/reschedule-drafts",
        headers=tool_headers(),
        body={
            "meetingId": managed_id,
            "title": "Day 3 rescheduled by confirmed Tool",
            "meetingType": "TECH_REVIEW",
            "roomId": 102,
            "startAt": proposed_start.isoformat(timespec="seconds"),
            "endAt": proposed_end.isoformat(timespec="seconds"),
            "requiredParticipantIds": [],
            "optionalParticipantIds": [],
            "expectedVersion": managed_version,
        },
    )
    reschedule_token = reschedule_draft["data"]["confirmationToken"]
    _, unchanged_before_confirm = request_json(
        "GET",
        f"{public_base}/api/v1/meetings/{managed_id}",
        headers=user_headers,
    )
    require(unchanged_before_confirm["data"]["roomId"] == 101, "reschedule draft changed meeting before confirm")

    reschedule_confirm_headers = tool_headers()
    reschedule_confirm_headers["Idempotency-Key"] = str(uuid.uuid4())
    reschedule_status, rescheduled = request_json(
        "POST",
        f"{business_base}/internal/v1/tools/reschedule-drafts/{quote(reschedule_token, safe='')}/confirm",
        headers=reschedule_confirm_headers,
    )
    require(reschedule_status == 200, "reschedule confirmation did not return HTTP 200")
    require(rescheduled["data"]["status"] == "SUCCESS", "reschedule confirmation failed")
    _, changed_after_confirm = request_json(
        "GET",
        f"{public_base}/api/v1/meetings/{managed_id}",
        headers=user_headers,
    )
    require(changed_after_confirm["data"]["roomId"] == 102, "reschedule confirmation did not change room")

    _, cancellation_preview = request_json(
        "POST",
        f"{business_base}/internal/v1/tools/cancellation-previews",
        headers=tool_headers(),
        body={"meetingId": managed_id},
    )
    cancellation_token = cancellation_preview["data"]["confirmationToken"]
    _, confirmed_before_cancel = request_json(
        "GET",
        f"{public_base}/api/v1/meetings/{managed_id}",
        headers=user_headers,
    )
    require(confirmed_before_cancel["data"]["status"] == "CONFIRMED", "cancel preview changed meeting before confirm")

    cancel_confirm_headers = tool_headers()
    cancel_confirm_headers["Idempotency-Key"] = str(uuid.uuid4())
    cancel_status, cancel_confirmed = request_json(
        "POST",
        f"{business_base}/internal/v1/tools/cancellation-previews/{quote(cancellation_token, safe='')}/confirm",
        headers=cancel_confirm_headers,
    )
    require(cancel_status == 200, "cancellation confirmation did not return HTTP 200")
    require(cancel_confirmed["data"]["status"] == "SUCCESS", "cancellation confirmation failed")
    _, cancelled_detail = request_json(
        "GET",
        f"{public_base}/api/v1/meetings/{managed_id}",
        headers=user_headers,
    )
    require(cancelled_detail["data"]["status"] == "CANCELLED", "confirmed cancellation did not cancel meeting")

    print(
        json.dumps(
            {
                "toolWithoutServiceToken": "REJECTED",
                "sseUnavailableBoundary": "AGENT_UNAVAILABLE",
                "badAgentAudience": "REJECTED",
                "toolParameterCap": "REJECTED",
                "resolveEmployees": "PASS",
                "freeBusy": "PASS",
                "availableRooms": "PASS",
                "draftSideEffect": "VERIFY_WITH_DB",
                "draftOnlyTitle": draft_only_title,
                "hotConfirm": "PENDING",
                "toolReplay": "SAME_REQUEST_NO",
                "finalStatus": booking["status"],
                "conflictFinalStatus": conflict_booking["status"],
                "meetingSource": meeting["data"]["source"],
                "recentMeeting": "PASS",
                "rescheduleDraftBeforeConfirm": "UNCHANGED",
                "rescheduleConfirm": "SUCCESS",
                "cancellationPreviewBeforeConfirm": "UNCHANGED",
                "cancellationConfirm": "SUCCESS",
                "cleanup": "CANCELLED",
                "requestNo": request_no,
                "conflictRequestNo": conflict_request_no,
                "meetingId": meeting_id,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, ApiFailure, KeyError, OSError, ValueError) as exc:
        print(f"Day 3 smoke failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
