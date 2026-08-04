#!/usr/bin/env python3
"""Run destructive-safe DeepSeek trajectories through Java's public API.

The script never reads a model key and never serializes access or confirmation
tokens.  Every business mutation is preceded by an authenticated public API
snapshot; meetings created by this script are cancelled through the normal
business endpoint when a trajectory stops early.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


CREATE_CAPACITY = "帮我预约2026年8月20日下午3点到4点的会议室，6个人，要白板，先给我候选。"
CREATE_NAMED = (
    "请安排张三和李四在2026年8月20日15:00到16:00开一小时架构评审，"
    "需要白板，先别替我确认。"
)
POLICY = "VIP会议室有哪些使用规则？请只根据制度回答并给引用。"
MODIFY_RECENT = (
    "把我刚才那个架构评审改到2026年8月20日16:00，其他不变，先给我看变更草案。"
)
CANCEL_9001 = "取消会议 ID 9001，先给我预览，不要直接取消。"


class TrajectoryFailure(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise TrajectoryFailure(message)


def _safe_error(error: BaseException) -> str:
    text = str(error).replace("\r", " ").replace("\n", " ")
    for marker in ("confirmationToken", "accessToken", "Authorization", "Bearer "):
        if marker in text:
            return "redacted transport failure"
    return text[:300]


def request_raw(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    timeout: int = 180,
) -> tuple[int, dict[str, Any], dict[str, str]]:
    actual_headers = {"Accept": "application/json", **(headers or {})}
    payload: bytes | None = None
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        actual_headers["Content-Type"] = "application/json; charset=utf-8"
    request = Request(url, data=payload, headers=actual_headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            parsed = json.loads(raw) if raw else {}
            require(isinstance(parsed, dict), f"{method} response was not a JSON object")
            return response.status, parsed, dict(response.headers.items())
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"code": "NON_JSON_ERROR"}
        require(isinstance(parsed, dict), f"{method} error was not a JSON object")
        return exc.code, parsed, dict(exc.headers.items())


def request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    status, payload, _ = request_raw(method, url, headers=headers, body=body)
    if not 200 <= status < 300:
        code = payload.get("code", f"HTTP_{status}")
        raise TrajectoryFailure(f"{method} returned {status}/{code}")
    data = payload.get("data")
    require(isinstance(data, dict), f"{method} response lacked a data object")
    return data


def request_sse(
    url: str, *, headers: dict[str, str], body: dict[str, Any]
) -> tuple[str, list[tuple[str, dict[str, Any]]], float]:
    actual_headers = {
        "Accept": "text/event-stream",
        "Content-Type": "application/json; charset=utf-8",
        **headers,
    }
    request = Request(
        url,
        data=json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        headers=actual_headers,
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urlopen(request, timeout=240) as response:
            run_id = response.headers.get("X-Run-Id", "")
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        raw_error = exc.read().decode("utf-8", errors="replace")
        try:
            code = json.loads(raw_error).get("code", f"HTTP_{exc.code}")
        except (json.JSONDecodeError, AttributeError):
            code = f"HTTP_{exc.code}"
        raise TrajectoryFailure(f"SSE returned {exc.code}/{code}") from exc
    latency_ms = round((time.perf_counter() - started) * 1000, 2)
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
        require(data_lines, f"SSE event {name} lacked data")
        value = json.loads("\n".join(data_lines))
        require(isinstance(value, dict), f"SSE event {name} was not an object")
        events.append((name, value))
    require(bool(run_id) and bool(events), "SSE response lacked run/event data")
    return run_id, events, latency_ms


def event(events: list[tuple[str, dict[str, Any]]], name: str) -> dict[str, Any]:
    values = [data for event_name, data in events if event_name == name]
    require(bool(values), f"missing SSE event {name}")
    return values[-1]


def start_run(
    public_base: str,
    headers: dict[str, str],
    message: str,
    *,
    thread_id: str | None = None,
) -> tuple[str, str, list[tuple[str, dict[str, Any]]], float]:
    run_id, events, latency_ms = request_sse(
        f"{public_base}/api/v1/agent/runs/stream",
        headers={**headers, "X-Trace-Id": f"trc_live_{uuid.uuid4().hex}"},
        body={
            "threadId": thread_id,
            "message": message,
            "clientRequestId": str(uuid.uuid4()),
        },
    )
    started = event(events, "run.started")
    actual_thread = started.get("threadId")
    require(isinstance(actual_thread, str) and actual_thread, "run.started lacked threadId")
    return run_id, actual_thread, events, latency_ms


def resume(
    public_base: str,
    headers: dict[str, str],
    run_id: str,
    hitl: dict[str, Any],
    action: str,
) -> tuple[list[tuple[str, dict[str, Any]]], float]:
    token = hitl.get("confirmationToken")
    require(isinstance(token, str) and token, "HITL event lacked transient token")
    resumed_run, events, latency_ms = request_sse(
        f"{public_base}/api/v1/agent/runs/{quote(run_id, safe='')}/resume",
        headers={**headers, "X-Trace-Id": f"trc_live_resume_{uuid.uuid4().hex}"},
        body={
            "action": action,
            "confirmationToken": token,
            "editedDraft": None,
            "feedback": None,
        },
    )
    require(resumed_run == run_id, "resume changed runId")
    return events, latency_ms


def meeting_window(public_base: str, headers: dict[str, str]) -> dict[int, dict[str, Any]]:
    query = urlencode(
        {
            "from": "2026-08-20T00:00:00+08:00",
            "to": "2026-08-22T00:00:00+08:00",
            "page": 1,
            "size": 100,
        }
    )
    data = request_json("GET", f"{public_base}/api/v1/meetings?{query}", headers=headers)
    items = data.get("items")
    require(isinstance(items, list), "meeting list lacked items")
    return {
        item["id"]: item
        for item in items
        if isinstance(item, dict) and isinstance(item.get("id"), int)
    }


def meeting_detail(public_base: str, headers: dict[str, str], meeting_id: int) -> dict[str, Any]:
    return request_json(
        "GET", f"{public_base}/api/v1/meetings/{meeting_id}", headers=headers
    )


def trace(public_base: str, headers: dict[str, str], run_id: str) -> dict[str, Any]:
    return request_json(
        "GET", f"{public_base}/api/v1/agent/runs/{quote(run_id, safe='')}/trace", headers=headers
    )


def trace_summary(value: dict[str, Any]) -> dict[str, Any]:
    run = value.get("run") if isinstance(value.get("run"), dict) else {}
    tools = value.get("toolCalls") if isinstance(value.get("toolCalls"), list) else []
    return {
        "status": run.get("status"),
        "intent": run.get("intent"),
        "provider": run.get("modelProvider"),
        "configuredModel": run.get("configuredModel"),
        "responseModels": run.get("responseModels", []),
        "promptVersion": run.get("promptVersion"),
        "schemaVersion": run.get("schemaVersion"),
        "modelCallCount": run.get("modelCallCount", 0),
        "toolCallCount": run.get("toolCallCount", 0),
        "tokenUsage": run.get("tokenUsage", {}),
        "durationMs": run.get("durationMs", 0),
        "tools": [item.get("toolName") for item in tools if isinstance(item, dict)],
    }


@dataclass
class Report:
    results: list[dict[str, Any]] = field(default_factory=list)
    latencies: list[float] = field(default_factory=list)

    def record(
        self,
        case_id: str,
        passed: bool,
        *,
        terminal: str,
        latency_ms: float,
        details: dict[str, Any] | None = None,
        failure: str | None = None,
    ) -> None:
        self.latencies.append(latency_ms)
        item: dict[str, Any] = {
            "caseId": case_id,
            "status": "PASS" if passed else "FAIL",
            "terminal": terminal,
            "latencyMs": latency_ms,
        }
        if details:
            item["details"] = details
        if failure:
            item["failure"] = failure[:300]
        self.results.append(item)

    def payload(self) -> dict[str, Any]:
        total = len(self.results)
        passed = sum(item["status"] == "PASS" for item in self.results)
        success_rate = round(passed / total, 4) if total else 0
        ordered = sorted(self.latencies)
        p50 = statistics.median(ordered) if ordered else 0
        p95 = ordered[min(len(ordered) - 1, max(0, int(len(ordered) * 0.95) - 1))] if ordered else 0
        return {
            "schemaVersion": "live-model-trajectory-v1",
            "mode": "live-model-trajectory",
            "provider": "deepseek",
            "suite": "required-natural-language",
            "status": "PASS" if total and success_rate >= 0.8 else "FAIL",
            "metrics": {
                "total": total,
                "passed": passed,
                "trajectorySuccess": success_rate,
                "p50LatencyMs": round(p50, 2),
                "p95LatencyMs": round(p95, 2),
            },
            "results": self.results,
            "limitations": [
                "This report covers the five required public-API inputs plus isolated mutation continuations; it is not the core-12 component corpus.",
                "The fixed ID 9001 case is an accurate negative result when that meeting is absent from retained data.",
            ],
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run real DeepSeek public-API trajectories.")
    parser.add_argument("--public-base", default="http://localhost")
    parser.add_argument("--output", type=Path, default=Path("artifacts/live-eval/trajectory.json"))
    args = parser.parse_args()
    public_base = args.public_base.rstrip("/")
    login = request_json(
        "POST",
        f"{public_base}/api/v1/auth/login",
        body={"username": "zhangsan", "password": "demo-password"},
    )
    access_token = login.get("accessToken")
    require(isinstance(access_token, str) and access_token, "login lacked access token")
    headers = {"Authorization": f"Bearer {access_token}"}
    report = Report()
    created_meeting_id: int | None = None
    thread_id: str | None = None

    try:
        # Capacity-only CREATE: the model must not invent names or write before REJECT.
        before = meeting_window(public_base, headers)
        started = time.perf_counter()
        try:
            run_id, _, events, latency = start_run(public_base, headers, CREATE_CAPACITY)
            hitl = event(events, "hitl.required")
            require(hitl.get("actionType") == "CREATE", "capacity CREATE returned wrong HITL type")
            draft = hitl.get("draft")
            require(isinstance(draft, dict), "capacity CREATE lacked draft")
            room_id = draft.get("roomId")
            require(isinstance(room_id, int), "capacity CREATE lacked roomId")
            room = request_json("GET", f"{public_base}/api/v1/rooms/{room_id}", headers=headers)
            require(int(room.get("capacity", 0)) >= 6, "candidate room capacity was below six")
            rejection, rejection_latency = resume(public_base, headers, run_id, hitl, "REJECT")
            require(event(rejection, "run.completed").get("status") == "CANCELLED", "REJECT did not cancel the run")
            require(meeting_window(public_base, headers) == before, "CREATE draft/REJECT changed meetings")
            summary = trace_summary(trace(public_base, headers, run_id))
            tools = summary["tools"]
            require("resolve_employees" not in tools, "headcount-only CREATE resolved invented names")
            require("get_employee_free_busy" in tools and "search_available_rooms" in tools, "CREATE missed organizer/room facts")
            report.record("required-create-capacity-reject", True, terminal="CANCELLED", latency_ms=latency + rejection_latency, details=summary)
        except Exception as exc:
            report.record("required-create-capacity-reject", False, terminal="ERROR", latency_ms=round((time.perf_counter() - started) * 1000, 2), failure=_safe_error(exc))

        # Named CREATE is the isolated accepted meeting used by mutation trajectories.
        started = time.perf_counter()
        try:
            run_id, thread_id, events, latency = start_run(public_base, headers, CREATE_NAMED)
            hitl = event(events, "hitl.required")
            require(hitl.get("actionType") == "CREATE", "named CREATE returned wrong HITL type")
            accepted, accept_latency = resume(public_base, headers, run_id, hitl, "ACCEPT")
            completed = event(accepted, "booking.completed")
            created_meeting_id = completed.get("meetingId")
            require(isinstance(created_meeting_id, int), "CREATE ACCEPT lacked meetingId")
            detail = meeting_detail(public_base, headers, created_meeting_id)
            require(detail.get("status") == "CONFIRMED", "accepted meeting was not confirmed")
            names = {item.get("displayName") for item in detail.get("participants", []) if isinstance(item, dict)}
            require({"张三", "李四"}.issubset(names), "named CREATE lost explicit participants")
            summary = trace_summary(trace(public_base, headers, run_id))
            report.record("required-create-named-accept", True, terminal="CONFIRMED", latency_ms=latency + accept_latency, details=summary)
        except Exception as exc:
            report.record("required-create-named-accept", False, terminal="ERROR", latency_ms=round((time.perf_counter() - started) * 1000, 2), failure=_safe_error(exc))

        # Policy must stay read-only and cite persisted policy chunks.
        started = time.perf_counter()
        try:
            run_id, _, events, latency = start_run(public_base, headers, POLICY)
            completed = event(events, "run.completed")
            citations = completed.get("citations")
            require(completed.get("status") == "SUCCEEDED", "policy did not succeed")
            require(isinstance(citations, list) and citations, "policy answer lacked citations")
            summary = trace_summary(trace(public_base, headers, run_id))
            require(summary["intent"] == "QUERY_POLICY", "policy route/intent was wrong")
            require(not summary["tools"], "policy query called business tools")
            report.record("required-policy-citations", True, terminal="SUCCEEDED", latency_ms=latency, details=summary)
        except Exception as exc:
            report.record("required-policy-citations", False, terminal="ERROR", latency_ms=round((time.perf_counter() - started) * 1000, 2), failure=_safe_error(exc))

        # Required natural-language recent-meeting MODIFY.  First REJECT proves no side effect.
        started = time.perf_counter()
        try:
            require(created_meeting_id is not None and thread_id is not None, "isolated meeting prerequisite failed")
            before_detail = meeting_detail(public_base, headers, created_meeting_id)
            run_id, _, events, latency = start_run(public_base, headers, MODIFY_RECENT, thread_id=thread_id)
            hitl = event(events, "hitl.required")
            require(hitl.get("actionType") == "RESCHEDULE", "MODIFY returned wrong HITL type")
            draft = hitl.get("draft")
            require(isinstance(draft, dict), "MODIFY lacked Before/After")
            original = draft.get("originalMeeting")
            proposed = draft.get("proposedMeeting")
            require(isinstance(original, dict) and isinstance(proposed, dict), "MODIFY lacked Before/After objects")
            require(original.get("id") == created_meeting_id, "MODIFY selected a different meeting")
            require(original.get("title") == proposed.get("title"), "MODIFY changed title despite '其他不变'")
            require(meeting_detail(public_base, headers, created_meeting_id) == before_detail, "MODIFY draft changed meeting before HITL")
            rejected, reject_latency = resume(public_base, headers, run_id, hitl, "REJECT")
            require(event(rejected, "run.completed").get("status") == "CANCELLED", "MODIFY REJECT failed")
            require(meeting_detail(public_base, headers, created_meeting_id) == before_detail, "MODIFY REJECT changed meeting")
            report.record("required-modify-recent-reject", True, terminal="CANCELLED", latency_ms=latency + reject_latency, details=trace_summary(trace(public_base, headers, run_id)))
        except Exception as exc:
            report.record("required-modify-recent-reject", False, terminal="ERROR", latency_ms=round((time.perf_counter() - started) * 1000, 2), failure=_safe_error(exc))

        # Repeat the same natural-language MODIFY and ACCEPT the isolated draft.
        started = time.perf_counter()
        try:
            require(created_meeting_id is not None and thread_id is not None, "isolated meeting prerequisite failed")
            before_detail = meeting_detail(public_base, headers, created_meeting_id)
            run_id, _, events, latency = start_run(public_base, headers, MODIFY_RECENT, thread_id=thread_id)
            hitl = event(events, "hitl.required")
            require(hitl.get("actionType") == "RESCHEDULE", "MODIFY ACCEPT lacked reschedule draft")
            require(meeting_detail(public_base, headers, created_meeting_id) == before_detail, "MODIFY changed meeting before ACCEPT")
            accepted, accept_latency = resume(public_base, headers, run_id, hitl, "ACCEPT")
            event(accepted, "booking.completed")
            after_detail = meeting_detail(public_base, headers, created_meeting_id)
            require(after_detail.get("version") == int(before_detail["version"]) + 1, "MODIFY ACCEPT did not advance version")
            require(str(after_detail.get("startAt", "")).startswith("2026-08-20T16:00"), "MODIFY ACCEPT used wrong start")
            report.record("required-modify-recent-accept", True, terminal="CONFIRMED", latency_ms=latency + accept_latency, details=trace_summary(trace(public_base, headers, run_id)))
        except Exception as exc:
            report.record("required-modify-recent-accept", False, terminal="ERROR", latency_ms=round((time.perf_counter() - started) * 1000, 2), failure=_safe_error(exc))

        # Execute the fixed 9001 input exactly.  Absence is an accurate negative, never a fake pass.
        started = time.perf_counter()
        try:
            run_id, _, events, latency = start_run(public_base, headers, CANCEL_9001)
            if events[-1][0] == "hitl.required":
                hitl = event(events, "hitl.required")
                require(hitl.get("actionType") == "CANCEL", "fixed cancel returned wrong HITL type")
                rejected, reject_latency = resume(public_base, headers, run_id, hitl, "REJECT")
                event(rejected, "run.completed")
                report.record("required-cancel-9001", True, terminal="CANCELLED", latency_ms=latency + reject_latency, details=trace_summary(trace(public_base, headers, run_id)))
            else:
                failed = event(events, "run.failed")
                code = failed.get("errorCode")
                require(code in {"MEETING_NOT_FOUND", "TARGET_MEETING_AMBIGUOUS"}, "fixed cancel failed for an unexpected reason")
                report.record("required-cancel-9001", False, terminal=str(code), latency_ms=latency, details=trace_summary(trace(public_base, headers, run_id)), failure="fixed meeting ID is not an accessible confirmed meeting in retained data")
        except Exception as exc:
            report.record("required-cancel-9001", False, terminal="ERROR", latency_ms=round((time.perf_counter() - started) * 1000, 2), failure=_safe_error(exc))

        # Dynamic ID proves CANCEL preview, REJECT, then ACCEPT against isolated data.
        for action in ("REJECT", "ACCEPT"):
            case_id = f"isolated-cancel-{action.lower()}"
            started = time.perf_counter()
            try:
                require(created_meeting_id is not None, "isolated meeting prerequisite failed")
                before_detail = meeting_detail(public_base, headers, created_meeting_id)
                message = f"取消会议 ID {created_meeting_id}，先给我预览，不要直接取消。"
                run_id, _, events, latency = start_run(public_base, headers, message, thread_id=thread_id)
                hitl = event(events, "hitl.required")
                require(hitl.get("actionType") == "CANCEL", "CANCEL returned wrong HITL type")
                draft = hitl.get("draft")
                require(isinstance(draft, dict) and isinstance(draft.get("meeting"), dict), "CANCEL lacked target snapshot")
                require(draft["meeting"].get("id") == created_meeting_id, "CANCEL preview selected wrong meeting")
                require(meeting_detail(public_base, headers, created_meeting_id) == before_detail, "CANCEL preview changed meeting")
                resumed, resume_latency = resume(public_base, headers, run_id, hitl, action)
                if action == "REJECT":
                    event(resumed, "run.completed")
                    require(meeting_detail(public_base, headers, created_meeting_id) == before_detail, "CANCEL REJECT changed meeting")
                    terminal = "CANCELLED_RUN"
                else:
                    event(resumed, "booking.completed")
                    require(meeting_detail(public_base, headers, created_meeting_id).get("status") == "CANCELLED", "CANCEL ACCEPT did not cancel meeting")
                    terminal = "CANCELLED_MEETING"
                report.record(case_id, True, terminal=terminal, latency_ms=latency + resume_latency, details=trace_summary(trace(public_base, headers, run_id)))
            except Exception as exc:
                report.record(case_id, False, terminal="ERROR", latency_ms=round((time.perf_counter() - started) * 1000, 2), failure=_safe_error(exc))
    finally:
        if created_meeting_id is not None:
            try:
                detail = meeting_detail(public_base, headers, created_meeting_id)
                if detail.get("status") == "CONFIRMED":
                    request_json(
                        "DELETE",
                        f"{public_base}/api/v1/meetings/{created_meeting_id}",
                        headers=headers,
                    )
            except (OSError, TrajectoryFailure):
                pass

    payload = report.payload()
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    require("confirmationToken" not in serialized and "accessToken" not in serialized, "report serialization leaked a token field")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(serialized, encoding="utf-8")
    print(json.dumps({"status": payload["status"], **payload["metrics"], "output": str(args.output)}, ensure_ascii=False))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyError, OSError, TrajectoryFailure, TypeError, ValueError) as exc:
        print(f"live trajectory failed: {_safe_error(exc)}", file=sys.stderr)
        raise SystemExit(1) from exc
