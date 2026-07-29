#!/usr/bin/env python3
"""Day 5 real-stack smoke for planning, HITL, recovery and HOT replanning.

The script uses the deterministic fixture provider, but every business transition
still crosses the public Java SSE gateway and Java Tool API.  It deliberately
uses only the seeded fictional employee and removes the confirmed smoke meeting.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


class SmokeFailure(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


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


def issue_agent_context_token(
    *, secret: str, user_id: int, trace_id: str, run_id: str
) -> str:
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": str(user_id),
        "roles": ["EMPLOYEE"],
        "traceId": trace_id,
        "runId": run_id,
        "aud": "agent-service",
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
) -> tuple[int, dict[str, Any], dict[str, str]]:
    payload = None
    actual_headers = dict(headers or {})
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        actual_headers["Content-Type"] = "application/json; charset=utf-8"
    request = Request(url, data=payload, headers=actual_headers, method=method)
    try:
        with urlopen(request, timeout=45) as response:
            return (
                response.status,
                json.loads(response.read().decode("utf-8")),
                dict(response.headers.items()),
            )
    except HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        raise SmokeFailure(f"{method} {url} returned HTTP {exc.code}: {body_text}") from exc


def request_sse(
    url: str, *, headers: dict[str, str], body: dict[str, Any]
) -> tuple[str, list[tuple[str, dict[str, Any]]]]:
    actual_headers = dict(headers)
    actual_headers["Content-Type"] = "application/json; charset=utf-8"
    actual_headers["Accept"] = "text/event-stream"
    request = Request(
        url,
        data=json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        headers=actual_headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=90) as response:
            run_id = response.headers.get("X-Run-Id", "")
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        raise SmokeFailure(f"agent SSE returned HTTP {exc.code}: {body_text}") from exc

    events: list[tuple[str, dict[str, Any]]] = []
    for block in raw.replace("\r\n", "\n").split("\n\n"):
        if not block.strip():
            continue
        event_name = "message"
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data_lines.append(line.removeprefix("data:").strip())
        require(data_lines, f"SSE event {event_name!r} has no data")
        try:
            data = json.loads("\n".join(data_lines))
        except json.JSONDecodeError as exc:
            raise SmokeFailure(f"SSE event {event_name!r} is not JSON") from exc
        require(isinstance(data, dict), f"SSE event {event_name!r} data is not an object")
        events.append((event_name, data))
    return run_id, events


def event_data(events: list[tuple[str, dict[str, Any]]], name: str) -> dict[str, Any]:
    matches = [data for event_name, data in events if event_name == name]
    require(bool(matches), f"missing SSE event {name}")
    return matches[-1]


def wait_for_booking_terminal(
    public_base: str, request_no: str, user_headers: dict[str, str]
) -> dict[str, Any]:
    """Wait for the existing Java booking-request query to reach a terminal state."""

    deadline = time.monotonic() + 45
    while True:
        _, response, _ = request_json(
            "GET",
            f"{public_base}/api/v1/booking-requests/{quote(request_no, safe='')}",
            headers=user_headers,
        )
        value = response.get("data")
        require(isinstance(value, dict), "booking-request response has no data object")
        if value.get("status") in {"SUCCESS", "CONFLICT", "FAILED"}:
            return value
        if time.monotonic() >= deadline:
            raise SmokeFailure("HOT booking request did not reach a terminal state")
        time.sleep(1)


def wait_for_recovery_view(
    public_base: str, run_id: str, user_headers: dict[str, str]
) -> dict[str, Any]:
    """Read Java's authenticated recovery proxy after a HOT callback replans."""

    deadline = time.monotonic() + 45
    while True:
        _, response, headers = request_json(
            "GET",
            f"{public_base}/api/v1/agent/runs/{quote(run_id, safe='')}",
            headers=user_headers,
        )
        cache_control = headers.get("Cache-Control", headers.get("Cache-control", ""))
        require(
            "no-store" in cache_control.lower(),
            "recovery view must forbid confirmation-token caching",
        )
        value = response.get("data")
        require(isinstance(value, dict), "recovery response has no data object")
        if value.get("status") == "WAITING_CONFIRMATION":
            return value
        if time.monotonic() >= deadline:
            raise SmokeFailure("HOT conflict did not produce a recovery draft")
        time.sleep(1)


def best_effort_cancel(public_base: str, meeting_id: int | None, headers: dict[str, str]) -> None:
    if meeting_id is None:
        return
    try:
        request_json("DELETE", f"{public_base}/api/v1/meetings/{meeting_id}", headers=headers)
    except (OSError, SmokeFailure):
        # Preserve the original smoke failure. A later operator can identify a
        # leftover fictional smoke meeting by its Day 5 title.
        return


def compose_command(args: argparse.Namespace, *command: str) -> list[str]:
    """Build an explicit Compose command for isolated Smoke projects.

    The default remains the developer's current Compose project.  Day 7's
    empty-volume Smoke supplies the project, env file and base Compose file so
    a checkpoint restart can never target the developer's running stack.
    """

    result = ["docker", "compose"]
    if args.compose_project:
        result.extend(["--project-name", args.compose_project])
    if args.compose_env_file is not None:
        result.extend(["--env-file", str(args.compose_env_file)])
    for compose_file in args.compose_file:
        result.extend(["--file", str(compose_file)])
    result.extend(command)
    return result


def wait_for_compose_agent_health(args: argparse.Namespace) -> None:
    """Wait for a restarted internal agent without publishing its port."""

    deadline = time.monotonic() + 60
    while True:
        result = subprocess.run(
            compose_command(args, "ps", "--format", "json", "agent-service"),
            check=False,
            capture_output=True,
            text=False,
            timeout=30,
        )
        # Docker Compose emits UTF-8 JSON even on Windows hosts whose active
        # console code page is GBK.  Decode explicitly so a non-ASCII workspace
        # path cannot crash the restart-verification helper itself.
        output = (result.stdout or b"").decode("utf-8", errors="replace")
        if (
            result.returncode == 0
            and re.search(r'"Service"\s*:\s*"agent-service"', output)
            and re.search(r'"Health"\s*:\s*"healthy"', output)
        ):
            return
        if time.monotonic() >= deadline:
            raise SmokeFailure("agent-service did not become healthy after checkpoint restart")
        time.sleep(1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-base", default="http://localhost")
    parser.add_argument("--agent-base", default="http://localhost:8000")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument(
        "--public-trace",
        action="store_true",
        help="verify persisted trace through Java's public proxy instead of a host-published internal Agent port",
    )
    parser.add_argument(
        "--compose-project",
        help="explicit Compose project used only for --restart-agent-service",
    )
    parser.add_argument(
        "--compose-env-file",
        type=Path,
        help="explicit Compose environment file used only for --restart-agent-service",
    )
    parser.add_argument(
        "--compose-file",
        type=Path,
        action="append",
        default=[],
        help="explicit Compose file used only for --restart-agent-service; repeat for overrides",
    )
    parser.add_argument(
        "--restart-agent-service",
        action="store_true",
        help="restart agent-service after initial HITL to verify Redis checkpoint recovery",
    )
    args = parser.parse_args()

    service_token = ""
    context_secret = ""
    if not args.public_trace:
        env = parse_env(args.env_file)
        service_token = env.get("INTERNAL_SERVICE_TOKEN", "")
        context_secret = env.get("AGENT_CONTEXT_JWT_SECRET", "")
        require(bool(service_token), "INTERNAL_SERVICE_TOKEN is missing")
        require(bool(context_secret), "AGENT_CONTEXT_JWT_SECRET is missing")

    public_base = args.public_base.rstrip("/")
    agent_base = args.agent_base.rstrip("/")
    _, login, _ = request_json(
        "POST",
        f"{public_base}/api/v1/auth/login",
        body={"username": "zhangsan", "password": "demo-password"},
    )
    access_token = str(login["data"]["accessToken"])
    user_id = int(login["data"]["user"]["id"])
    user_headers = {"Authorization": f"Bearer {access_token}"}

    initial_trace_id = f"trc_day5_initial_{uuid.uuid4().hex}"
    run_id, initial_events = request_sse(
        f"{public_base}/api/v1/agent/runs/stream",
        headers={**user_headers, "X-Trace-Id": initial_trace_id},
        body={
            "threadId": None,
            "message": "下周三下午帮张三安排一个90分钟架构评审，要大屏",
            "clientRequestId": str(uuid.uuid4()),
        },
    )
    require(bool(run_id), "Java proxy did not return X-Run-Id for initial run")
    require(initial_events[0][0] == "run.started", "initial stream must start with run.started")
    require(initial_events[-1][0] == "hitl.required", "initial stream must pause for HITL")
    require(
        all(data.get("runId") == run_id for _, data in initial_events),
        "initial SSE contains a mismatched runId",
    )
    candidate_event = event_data(initial_events, "plan.candidates")
    candidates = candidate_event.get("candidates")
    require(isinstance(candidates, list) and candidates, "planner returned no candidates")
    require(len(candidates) <= 3, "planner returned more than three candidates")
    require(
        [candidate.get("totalCost") for candidate in candidates]
        == sorted(candidate.get("totalCost") for candidate in candidates),
        "candidates are not sorted by total cost",
    )
    initial_hitl = event_data(initial_events, "hitl.required")
    initial_confirmation = str(initial_hitl.get("confirmationToken", ""))
    require(bool(initial_confirmation), "initial draft did not provide confirmationToken")

    if args.restart_agent_service:
        subprocess.run(
            compose_command(args, "restart", "agent-service"),
            check=True,
            timeout=60,
        )
        if args.compose_project:
            wait_for_compose_agent_health(args)
        else:
            deadline = time.monotonic() + 60
            while True:
                try:
                    _, health, _ = request_json("GET", f"{agent_base}/internal/v1/health")
                    if health.get("status") in {"UP", "DEGRADED"}:
                        break
                except (OSError, SmokeFailure):
                    pass
                if time.monotonic() >= deadline:
                    raise SmokeFailure("agent-service did not become healthy after checkpoint restart")
                time.sleep(1)

    # Choose an already validated alternative when possible. This proves EDIT
    # re-enters deterministic scheduling instead of sending an edited value to a
    # WRITE Tool directly.
    edited_candidate = candidates[1] if len(candidates) > 1 else candidates[0]
    require(isinstance(edited_candidate, dict), "candidate has invalid shape")
    edit_trace_id = f"trc_day5_edit_{uuid.uuid4().hex}"
    resumed_run_id, edit_events = request_sse(
        f"{public_base}/api/v1/agent/runs/{quote(run_id, safe='')}/resume",
        headers={**user_headers, "X-Trace-Id": edit_trace_id},
        body={
            "action": "EDIT",
            "confirmationToken": initial_confirmation,
            "editedDraft": {
                "roomId": edited_candidate["roomId"],
                "startAt": edited_candidate["startAt"],
            },
            "feedback": None,
        },
    )
    require(resumed_run_id == run_id, "resume response changed runId")
    require(edit_events[0][0] == "run.resumed", "EDIT stream must start with run.resumed")
    require(edit_events[-1][0] == "hitl.required", "EDIT must return to HITL")
    require(
        not any(
            name == "tool.call" and data.get("riskLevel") == "WRITE" for name, data in edit_events
        ),
        "EDIT invoked a WRITE Tool before renewed confirmation",
    )
    edited_hitl = event_data(edit_events, "hitl.required")
    edited_confirmation = str(edited_hitl.get("confirmationToken", ""))
    require(bool(edited_confirmation), "edited draft did not provide confirmationToken")

    accept_trace_id = f"trc_day5_accept_{uuid.uuid4().hex}"
    accepted_run_id, accept_events = request_sse(
        f"{public_base}/api/v1/agent/runs/{quote(run_id, safe='')}/resume",
        headers={**user_headers, "X-Trace-Id": accept_trace_id},
        body={
            "action": "ACCEPT",
            "confirmationToken": edited_confirmation,
            "editedDraft": None,
            "feedback": None,
        },
    )
    require(accepted_run_id == run_id, "ACCEPT response changed runId")
    require(accept_events[0][0] == "run.resumed", "ACCEPT stream must start with run.resumed")
    require(accept_events[-1][0] == "run.completed", "synchronous confirmation did not complete")
    booking_completed = event_data(accept_events, "booking.completed")
    meeting_id = booking_completed.get("meetingId")
    require(isinstance(meeting_id, int) and meeting_id > 0, "confirmation did not return a meetingId")
    require(
        any(
            name == "tool.call"
            and data.get("toolName") == "confirm_booking"
            and data.get("riskLevel") == "WRITE"
            and data.get("status") == "SUCCEEDED"
            for name, data in accept_events
        ),
        "ACCEPT did not execute the confirmed WRITE Tool",
    )

    if args.public_trace:
        _, trace_response, _ = request_json(
            "GET",
            f"{public_base}/api/v1/agent/runs/{quote(run_id, safe='')}/trace",
            headers=user_headers,
        )
        trace = trace_response.get("data")
        require(isinstance(trace, dict), "public trace response has no data object")
    else:
        context_token = issue_agent_context_token(
            secret=context_secret, user_id=user_id, trace_id=accept_trace_id, run_id=run_id
        )
        _, trace, _ = request_json(
            "GET",
            f"{agent_base}/internal/v1/agent-runs/{quote(run_id, safe='')}/trace",
            headers={
                "Authorization": f"Bearer {context_token}",
                "X-Service-Token": service_token,
                "X-Trace-Id": accept_trace_id,
                "X-Run-Id": run_id,
            },
        )
    run = trace.get("run", {})
    require(run.get("status") == "SUCCEEDED", "persisted run is not SUCCEEDED")
    require(int(run.get("toolCallCount", 0)) >= 5, "Day 5 run did not persist its Tool calls")

    _, meeting, _ = request_json(
        "GET", f"{public_base}/api/v1/meetings/{meeting_id}", headers=user_headers
    )
    require(meeting.get("data", {}).get("status") == "CONFIRMED", "meeting is not CONFIRMED")
    _, cancelled, _ = request_json(
        "DELETE", f"{public_base}/api/v1/meetings/{meeting_id}", headers=user_headers
    )
    require(
        cancelled.get("data", {}).get("status") == "CANCELLED",
        "Day 5 smoke cleanup did not cancel its meeting",
    )

    # Force the Day 5 HOT recovery path without touching existing data. A
    # ten-person request excludes room 101; room 103 has lower capacity waste
    # than room 102 and is HOT, while room 102 remains a valid post-conflict
    # alternative. The competing manual meeting uses admin so Zhangsan remains
    # a valid REQUIRED participant for the replanned draft.
    _, admin_login, _ = request_json(
        "POST",
        f"{public_base}/api/v1/auth/login",
        body={"username": "admin", "password": "demo-password"},
    )
    admin_headers = {"Authorization": f"Bearer {admin_login['data']['accessToken']}"}
    hot_run_id = ""
    blocker_meeting_id: int | None = None
    replanned_meeting_id: int | None = None
    try:
        hot_trace_id = f"trc_day5_hot_{uuid.uuid4().hex}"
        hot_run_id, hot_events = request_sse(
            f"{public_base}/api/v1/agent/runs/stream",
            headers={**user_headers, "X-Trace-Id": hot_trace_id},
            body={
                "threadId": None,
                "message": "下周三下午帮张三安排一个90分钟架构评审，10人，要大屏",
                "clientRequestId": str(uuid.uuid4()),
            },
        )
        require(bool(hot_run_id), "HOT stream did not return X-Run-Id")
        require(
            hot_events[-1][0] == "hitl.required",
            "HOT stream did not pause for HITL; received "
            + json.dumps(
                [
                    {
                        "event": name,
                        "code": data.get("code"),
                        "status": data.get("status"),
                    }
                    for name, data in hot_events
                ],
                ensure_ascii=False,
            ),
        )
        hot_hitl = event_data(hot_events, "hitl.required")
        hot_draft = hot_hitl.get("draft")
        require(isinstance(hot_draft, dict), "HOT HITL event has no draft")
        require(hot_draft.get("roomId") == 103, "fixture did not select HOT room 103")
        hot_confirmation = str(hot_hitl.get("confirmationToken", ""))
        require(bool(hot_confirmation), "HOT draft lacks a confirmationToken")

        _, blocker, _ = request_json(
            "POST",
            f"{public_base}/api/v1/meetings",
            headers={**admin_headers, "Idempotency-Key": f"idem_day5_block_{uuid.uuid4().hex}"},
            body={
                "title": "Day 5 HOT conflict blocker",
                "meetingType": "ARCHITECTURE_REVIEW",
                "roomId": 103,
                "startAt": hot_draft["startAt"],
                "endAt": hot_draft["endAt"],
                "requiredParticipantIds": [],
                "optionalParticipantIds": [],
                "createVideoConference": False,
            },
        )
        blocker_value = blocker.get("data", {})
        blocker_meeting_id = blocker_value.get("id")
        require(
            isinstance(blocker_meeting_id, int) and blocker_meeting_id > 0,
            "failed to create HOT conflict blocker",
        )

        hot_accept_trace_id = f"trc_day5_hot_accept_{uuid.uuid4().hex}"
        resumed_hot_run_id, hot_accept_events = request_sse(
            f"{public_base}/api/v1/agent/runs/{quote(hot_run_id, safe='')}/resume",
            headers={**user_headers, "X-Trace-Id": hot_accept_trace_id},
            body={
                "action": "ACCEPT",
                "confirmationToken": hot_confirmation,
                "editedDraft": None,
                "feedback": None,
            },
        )
        require(resumed_hot_run_id == hot_run_id, "HOT ACCEPT changed runId")
        require(
            hot_accept_events[-1][0] == "booking.pending",
            f"HOT ACCEPT did not enter PENDING for run {hot_run_id} / room {hot_draft.get('roomId')}; received "
            + json.dumps(
                [
                    {
                        "event": name,
                        "code": data.get("code"),
                        "status": data.get("status"),
                    }
                    for name, data in hot_accept_events
                ],
                ensure_ascii=False,
            ),
        )
        pending = event_data(hot_accept_events, "booking.pending")
        request_no = pending.get("requestNo")
        require(isinstance(request_no, str) and request_no, "HOT PENDING has no requestNo")
        terminal = wait_for_booking_terminal(public_base, request_no, user_headers)
        require(terminal.get("status") == "CONFLICT", "HOT competing request did not conflict")

        recovery = wait_for_recovery_view(public_base, hot_run_id, user_headers)
        recovery_candidates = recovery.get("candidates")
        recovery_draft = recovery.get("draft")
        recovery_token = recovery.get("confirmationToken")
        require(
            isinstance(recovery_candidates, list) and recovery_candidates,
            "HOT callback did not return replanned candidates",
        )
        require(isinstance(recovery_draft, dict), "HOT recovery has no draft")
        require(recovery_draft.get("roomId") == 102, "HOT conflict did not replan to room 102")
        require(
            isinstance(recovery_token, str) and recovery_token,
            "HOT recovery lacks a fresh confirmationToken",
        )

        hot_final_trace_id = f"trc_day5_hot_final_{uuid.uuid4().hex}"
        final_hot_run_id, final_hot_events = request_sse(
            f"{public_base}/api/v1/agent/runs/{quote(hot_run_id, safe='')}/resume",
            headers={**user_headers, "X-Trace-Id": hot_final_trace_id},
            body={
                "action": "ACCEPT",
                "confirmationToken": recovery_token,
                "editedDraft": None,
                "feedback": None,
            },
        )
        require(final_hot_run_id == hot_run_id, "replanned ACCEPT changed runId")
        final_completed = event_data(final_hot_events, "booking.completed")
        replanned_meeting_id = final_completed.get("meetingId")
        require(
            isinstance(replanned_meeting_id, int) and replanned_meeting_id > 0,
            "replanned confirmation did not create a meeting",
        )
    finally:
        best_effort_cancel(public_base, replanned_meeting_id, user_headers)
        best_effort_cancel(public_base, blocker_meeting_id, admin_headers)

    print(
        json.dumps(
            {
                "day5GoldenPath": "PASS",
                "runId": run_id,
                "candidateCount": len(candidates),
                "initialEvents": [name for name, _ in initial_events],
                "editEvents": [name for name, _ in edit_events],
                "acceptEvents": [name for name, _ in accept_events],
                "meetingId": meeting_id,
                "cleanup": "CANCELLED",
                "hotConflictRecovery": "PASS",
                "hotRunId": hot_run_id,
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
        print(f"Day 5 smoke failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
