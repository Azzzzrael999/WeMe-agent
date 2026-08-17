#!/usr/bin/env python3
"""Adversarial product scenarios through Java's public API.

The runner uses fictional demo users only.  It never persists access tokens,
confirmation tokens, or raw SSE payloads.  Every HITL draft is rejected and
existing meetings used for reschedule/cancel checks are compared before and
after the run so the evaluation leaves no intentional business mutation.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

Terminal = Literal["WAITING_INPUT", "HITL", "SUCCEEDED"]


class ScenarioFailure(RuntimeError):
    pass


@dataclass(frozen=True)
class DialogueTurn:
    message: str
    expected_terminal: Terminal
    required_events: tuple[str, ...] = ()


@dataclass(frozen=True)
class DialogueScenario:
    case_id: str
    purpose: str
    turns: tuple[DialogueTurn, ...]
    expected_intent: str
    expected_action: str | None = None
    required_tools: tuple[str, ...] = ()
    allowed_citations: tuple[str, ...] | None = None
    allowed_citation_prefixes: tuple[str, ...] = ()
    require_no_citations: bool = False
    protected_meeting_ids: tuple[int, ...] = ()


@dataclass
class ScenarioContext:
    public_base: str
    headers: dict[str, str]
    protected_before: dict[int, dict[str, Any]]
    results: list[dict[str, Any]] = field(default_factory=list)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ScenarioFailure(message)


def safe_error(error: BaseException) -> str:
    value = str(error).replace("\r", " ").replace("\n", " ")
    if any(
        marker in value
        for marker in ("confirmationToken", "accessToken", "Authorization", "Bearer ")
    ):
        return "redacted evaluation failure"
    return value[:300]


def request_raw(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    timeout: int = 240,
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
            require(isinstance(parsed, dict), f"{method} response is not a JSON object")
            return response.status, parsed, dict(response.headers.items())
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"code": "NON_JSON_ERROR"}
        require(isinstance(parsed, dict), f"{method} error is not a JSON object")
        return exc.code, parsed, dict(exc.headers.items())


def request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    status, payload, response_headers = request_raw(method, url, headers=headers, body=body)
    if not 200 <= status < 300:
        raise ScenarioFailure(f"{method} returned {status}/{payload.get('code', 'UNKNOWN')}")
    data = payload.get("data")
    require(isinstance(data, dict), f"{method} response lacks data")
    return data, response_headers


def request_sse(
    url: str,
    *,
    headers: dict[str, str],
    body: dict[str, Any],
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
        raise ScenarioFailure(f"SSE returned {exc.code}/{code}") from exc
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
        require(data_lines, f"SSE event {name} has no data")
        data = json.loads("\n".join(data_lines))
        require(isinstance(data, dict), f"SSE event {name} data is not an object")
        events.append((name, data))
    require(events, "SSE response is empty")
    return run_id, events, round((time.perf_counter() - started) * 1000, 2)


def latest_event(events: list[tuple[str, dict[str, Any]]], name: str) -> dict[str, Any] | None:
    values = [data for event_name, data in events if event_name == name]
    return values[-1] if values else None


def terminal_of(events: list[tuple[str, dict[str, Any]]]) -> Terminal | str:
    name, data = events[-1]
    if name == "hitl.required":
        return "HITL"
    if name == "run.completed":
        return "WAITING_INPUT" if data.get("status") == "WAITING_USER_INPUT" else "SUCCEEDED"
    if name == "run.failed":
        return f"FAILED:{data.get('errorCode', 'UNKNOWN')}"
    return f"UNEXPECTED:{name}"


def event_summary(events: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    final_name, final_data = events[-1]
    candidates = latest_event(events, "plan.candidates")
    unsat = latest_event(events, "plan.unsat")
    requirement = latest_event(events, "requirement.updated")
    completed = latest_event(events, "run.completed")
    citations = completed.get("citations", []) if completed is not None else []
    return {
        "events": [name for name, _ in events],
        "terminalEvent": final_name,
        "terminalStatus": final_data.get("status"),
        "candidateCount": len(candidates.get("candidates", [])) if candidates else 0,
        "unsatCategory": (unsat.get("unsatAnalysis", {}).get("category") if unsat else None),
        "requirementRevision": requirement.get("revision") if requirement else None,
        "citationIds": [item.get("chunkId") for item in citations if isinstance(item, dict)],
    }


def login(public_base: str, username: str, password: str) -> dict[str, str]:
    data, _ = request_json(
        "POST",
        f"{public_base}/api/v1/auth/login",
        body={"username": username, "password": password},
    )
    token = data.get("accessToken")
    require(isinstance(token, str) and token, "login returned no access token")
    return {"Authorization": f"Bearer {token}"}


def list_owned_meetings(
    public_base: str, headers: dict[str, str]
) -> tuple[int, list[dict[str, Any]]]:
    me, _ = request_json("GET", f"{public_base}/api/v1/auth/me", headers=headers)
    user_id = me.get("id")
    require(isinstance(user_id, int), "auth/me returned no user id")
    zone = ZoneInfo("Asia/Shanghai")
    window_start = datetime.now(zone).replace(hour=0, minute=0, second=0, microsecond=0)
    window_end = window_start + timedelta(days=14)
    query = urlencode(
        {
            "from": window_start.isoformat(),
            "to": window_end.isoformat(),
            "status": "CONFIRMED",
            "page": 1,
            "size": 100,
        }
    )
    data, _ = request_json("GET", f"{public_base}/api/v1/meetings?{query}", headers=headers)
    items = data.get("items")
    require(isinstance(items, list), "meeting list lacks items")
    return user_id, [
        item for item in items if isinstance(item, dict) and item.get("organizerId") == user_id
    ]


def meeting_snapshot(public_base: str, headers: dict[str, str], meeting_id: int) -> dict[str, Any]:
    data, _ = request_json("GET", f"{public_base}/api/v1/meetings/{meeting_id}", headers=headers)
    return {
        key: data.get(key)
        for key in ("id", "title", "roomId", "startAt", "endAt", "status", "version")
    }


def chinese_date(value: datetime) -> str:
    return f"{value.month}月{value.day}日"


def format_clock(value: datetime) -> str:
    return value.strftime("%H:%M")


def build_scenarios(owned: list[dict[str, Any]]) -> tuple[DialogueScenario, ...]:
    zone = ZoneInfo("Asia/Shanghai")
    now = datetime.now(zone)
    first_day = (now + timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
    second_day = first_day + timedelta(days=1)
    third_day = first_day + timedelta(days=2)
    occupied_dates = {str(item.get("startAt", ""))[:10] for item in owned}
    ambiguous_clock_day = next(
        (
            candidate
            for offset in range(7, 31)
            if (candidate := now + timedelta(days=offset)).date().isoformat()
            not in occupied_dates
        ),
        third_day,
    )
    scenarios: list[DialogueScenario] = [
        DialogueScenario(
            case_id="create-formal-hard-constraints",
            purpose="正式表达、多人、容量和双设备硬约束",
            turns=(
                DialogueTurn(
                    f"请安排李四和周经理在{chinese_date(first_day)}14:00至17:00之间开60分钟支付网关发布准备会，至少8人，需要大屏和白板。先给候选，不要直接确认。",
                    "HITL",
                    ("plan.candidates", "hitl.required"),
                ),
            ),
            expected_intent="CREATE_MEETING",
            expected_action="CREATE",
            required_tools=("resolve_employees", "search_available_rooms", "create_booking_draft"),
        ),
        DialogueScenario(
            case_id="create-colloquial-synonyms",
            purpose="口语、半小时、投屏同义表达",
            turns=(
                DialogueTurn(
                    f"麻烦{chinese_date(second_day)}午后帮我跟李四碰一下，半小时就行，四五个人，找个能投屏的屋，先让我挑。",
                    "HITL",
                    ("plan.candidates", "hitl.required"),
                ),
            ),
            expected_intent="CREATE_MEETING",
            expected_action="CREATE",
            required_tools=("resolve_employees", "search_available_rooms", "create_booking_draft"),
        ),
        DialogueScenario(
            case_id="create-missing-then-complete",
            purpose="信息不足后一次补齐，复用同一 Run",
            turns=(
                DialogueTurn("帮我安排一个上线评审。", "WAITING_INPUT", ("requirement.updated",)),
                DialogueTurn(
                    f"定在{chinese_date(third_day)}下午1点到5点这个范围，开90分钟；李四、赵六必须参加，8个人，要大屏，其他没要求。",
                    "HITL",
                    ("requirement.updated", "plan.candidates", "hitl.required"),
                ),
            ),
            expected_intent="CREATE_MEETING",
            expected_action="CREATE",
            required_tools=("resolve_employees", "search_available_rooms", "create_booking_draft"),
        ),
        DialogueScenario(
            case_id="create-ambiguous-clock-clarified",
            purpose="单独‘2点’歧义与自然语言澄清",
            turns=(
                DialogueTurn(
                    f"{chinese_date(ambiguous_clock_day)}2点帮我开一小时会，只有我参加。",
                    "WAITING_INPUT",
                    ("requirement.updated",),
                ),
                DialogueTurn(
                    "是下午两点，4个人，不需要额外设备。",
                    "HITL",
                    ("requirement.updated", "hitl.required"),
                ),
            ),
            expected_intent="CREATE_MEETING",
            expected_action="CREATE",
            required_tools=("search_available_rooms", "create_booking_draft"),
        ),
        DialogueScenario(
            case_id="create-participant-delta",
            purpose="续聊中删除、增加参会人并关闭可选要求",
            turns=(
                DialogueTurn(
                    "帮我和李四、赵六约个会，周经理可选。",
                    "WAITING_INPUT",
                    ("requirement.updated",),
                ),
                DialogueTurn(
                    f"调整参会人：赵六不参加了，加上孙琪必须参加；{chinese_date(first_day)}下午1点到5点内找60分钟，6个人，要白板，其他没有要求。",
                    "HITL",
                    ("requirement.updated", "hitl.required"),
                ),
            ),
            expected_intent="CREATE_MEETING",
            expected_action="CREATE",
            required_tools=("resolve_employees", "search_available_rooms", "create_booking_draft"),
        ),
        DialogueScenario(
            case_id="create-my-team-scope",
            purpose="‘我的小组’必须由服务端人员范围解析",
            turns=(
                DialogueTurn(
                    f"给我的小组约个{chinese_date(second_day)}下午的60分钟周会，至少12人，要白板，房间你挑，先给方案。",
                    "HITL",
                    ("plan.candidates", "hitl.required"),
                ),
            ),
            expected_intent="CREATE_MEETING",
            expected_action="CREATE",
            required_tools=(
                "resolve_participant_scope",
                "search_available_rooms",
                "create_booking_draft",
            ),
        ),
        DialogueScenario(
            case_id="create-mixed-language",
            purpose="中英混合、简写 sync 和设备偏好",
            turns=(
                DialogueTurn(
                    f"Please book a 30-minute sync with 李四 on {second_day:%Y-%m-%d} "
                    "afternoon, 4 people, whiteboard required. Show options first.",
                    "HITL",
                    ("plan.candidates", "hitl.required"),
                ),
            ),
            expected_intent="CREATE_MEETING",
            expected_action="CREATE",
            required_tools=("resolve_employees", "search_available_rooms", "create_booking_draft"),
        ),
        DialogueScenario(
            case_id="find-common-time-read-only",
            purpose="只找共同时间，不创建草案",
            turns=(
                DialogueTurn(
                    f"帮我看看李四和周经理在{chinese_date(first_day)}上午9点到12点有没有一起空出的60分钟，只查时间，不预约。",
                    "SUCCEEDED",
                    ("plan.candidates", "run.completed"),
                ),
            ),
            expected_intent="FIND_COMMON_TIME",
            required_tools=("resolve_employees", "get_employee_free_busy"),
        ),
        DialogueScenario(
            case_id="recommend-room-read-only",
            purpose="只推荐会议室，不产生草案",
            turns=(
                DialogueTurn(
                    f"{chinese_date(second_day)}13:00到17:00，8个人，给我找带大屏和白板的会议室，只推荐，不要预约。",
                    "SUCCEEDED",
                    ("plan.candidates", "run.completed"),
                ),
            ),
            expected_intent="RECOMMEND_ROOM",
            required_tools=("search_available_rooms",),
        ),
        DialogueScenario(
            case_id="policy-vip-grounded",
            purpose="VIP 制度问答必须引用相关证据",
            turns=(
                DialogueTurn(
                    "接待重要客户能直接用VIP会议室吗？请只按公司制度回答，并告诉我依据。",
                    "SUCCEEDED",
                    ("run.completed",),
                ),
            ),
            expected_intent="QUERY_POLICY",
            allowed_citations=(
                "chunk_vip_room_v1",
            ),
            allowed_citation_prefixes=("chunk_doc_vip_executive_room_policy_",),
        ),
        DialogueScenario(
            case_id="policy-architecture-grounded",
            purpose="架构评审设备规则不得引用无关制度",
            turns=(
                DialogueTurn(
                    "做架构评审时，会议室设备有什么硬性或建议要求？只说知识库能证明的。",
                    "SUCCEEDED",
                    ("run.completed",),
                ),
            ),
            expected_intent="QUERY_POLICY",
            allowed_citations=(
                "chunk_architecture_review_v1",
                "chunk_room_equipment_v1",
            ),
            allowed_citation_prefixes=("chunk_doc_architecture_review_standard_",),
        ),
        DialogueScenario(
            case_id="policy-unknown-honesty",
            purpose="知识库无答案时不得硬编制度或挂无关引用",
            turns=(
                DialogueTurn(
                    "公司是不是规定每月最后一个周五开会必须穿蓝色衣服？没有制度依据就直接说没查到。",
                    "SUCCEEDED",
                    ("run.completed",),
                ),
            ),
            expected_intent="QUERY_POLICY",
            require_no_citations=True,
        ),
    ]

    owned = sorted(owned, key=lambda item: str(item.get("startAt", "")))
    if owned:
        modify = next((item for item in owned if "架构" in str(item.get("title", ""))), owned[0])
        cancel = next((item for item in owned if item.get("id") != modify.get("id")), None)
        modify_id = int(modify["id"])
        modify_start = datetime.fromisoformat(str(modify["startAt"]))
        destination = modify_start + timedelta(days=1)
        scenarios.append(
            DialogueScenario(
                case_id="modify-explicit-id-reject",
                purpose="明确会议 ID、继承原约束、预览后拒绝",
                turns=(
                    DialogueTurn(
                        f"把会议ID {modify_id}挪到{chinese_date(destination)}上午10点，"
                        "时长、人员和设备都不变，先给我看变更草案。",
                        "HITL",
                        ("plan.candidates", "hitl.required"),
                    ),
                ),
                expected_intent="MODIFY_MEETING",
                expected_action="RESCHEDULE",
                required_tools=("get_recent_meeting", "create_reschedule_draft"),
                protected_meeting_ids=(modify_id,),
            )
        )
        if cancel is not None:
            cancel_id = int(cancel["id"])
            scenarios.append(
                DialogueScenario(
                    case_id="cancel-colloquial-reject",
                    purpose="口语取消目标识别与取消预览",
                    turns=(
                        DialogueTurn(
                            f"把 {cancel_id} 号会议撤掉，不过先让我看清楚会取消哪一场，别直接动。",
                            "HITL",
                            ("hitl.required",),
                        ),
                    ),
                    expected_intent="CANCEL_MEETING",
                    expected_action="CANCEL",
                    required_tools=("get_recent_meeting", "create_cancellation_preview"),
                    protected_meeting_ids=(cancel_id,),
                )
            )

    by_date: dict[str, list[dict[str, Any]]] = {}
    for item in owned:
        by_date.setdefault(str(item.get("startAt", ""))[:10], []).append(item)
    ambiguous_group = next((items for items in by_date.values() if len(items) >= 2), None)
    if ambiguous_group is not None:
        group = sorted(ambiguous_group, key=lambda item: str(item["startAt"]))
        target = next((item for item in group if "架构" in str(item.get("title", ""))), group[0])
        start = datetime.fromisoformat(str(group[0]["startAt"]))
        end = max(datetime.fromisoformat(str(item["endAt"])) for item in group)
        destination = start + timedelta(days=1)
        target_title = str(target["title"])
        protected = tuple(int(item["id"]) for item in group)
        scenarios.extend(
            (
                DialogueScenario(
                    case_id="modify-ambiguous-target-clarified",
                    purpose="同日多场会议目标消歧后再生成改期草案",
                    turns=(
                        DialogueTurn(
                            f"把{chinese_date(start)}下午的会挪到第二天上午，其他照旧。",
                            "WAITING_INPUT",
                            ("requirement.updated",),
                        ),
                        DialogueTurn(
                            f"是“{target_title}”这场，改到{chinese_date(destination)}上午10点，其他不变。",
                            "HITL",
                            ("requirement.updated", "hitl.required"),
                        ),
                    ),
                    expected_intent="MODIFY_MEETING",
                    expected_action="RESCHEDULE",
                    required_tools=("get_recent_meeting", "create_reschedule_draft"),
                    protected_meeting_ids=protected,
                ),
                DialogueScenario(
                    case_id="unsat-then-relax-same-run",
                    purpose="必需人员连续忙碌导致无解，同一 Run 放宽日期",
                    turns=(
                        DialogueTurn(
                            f"我必须参加，想在{chinese_date(start)}{format_clock(start)}到"
                            f"{format_clock(end)}之间开60分钟会议，4个人，"
                            "无设备要求；如果排不开请说具体原因。",
                            "WAITING_INPUT",
                            ("plan.unsat", "run.completed"),
                        ),
                        DialogueTurn(
                            f"那改到{chinese_date(destination)}上午9点到12点这个范围，时长和其他要求不变。",
                            "HITL",
                            ("requirement.updated", "plan.candidates", "hitl.required"),
                        ),
                    ),
                    expected_intent="CREATE_MEETING",
                    expected_action="CREATE",
                    required_tools=(
                        "get_employee_free_busy",
                        "search_available_rooms",
                        "create_booking_draft",
                    ),
                    protected_meeting_ids=protected,
                ),
            )
        )
    return tuple(scenarios)


def assert_meeting_unchanged(context: ScenarioContext, meeting_id: int) -> None:
    before = context.protected_before[meeting_id]
    after = meeting_snapshot(context.public_base, context.headers, meeting_id)
    require(after == before, f"meeting {meeting_id} changed during a reject-only scenario")


def reject_draft(
    context: ScenarioContext,
    run_id: str,
    confirmation_token: str,
) -> list[tuple[str, dict[str, Any]]]:
    resumed_run_id, events, _ = request_sse(
        f"{context.public_base}/api/v1/agent/runs/{quote(run_id, safe='')}/resume",
        headers={
            **context.headers,
            "X-Trace-Id": f"trc_scenario_reject_{uuid.uuid4().hex}",
        },
        body={
            "action": "REJECT",
            "confirmationToken": confirmation_token,
            "editedDraft": None,
            "feedback": "场景测试结束，不执行写入。",
        },
    )
    require(resumed_run_id == run_id, "REJECT changed runId")
    require(events[-1][0] == "run.completed", "REJECT did not complete the run")
    return events


def execute_scenario(context: ScenarioContext, scenario: DialogueScenario) -> dict[str, Any]:
    started = time.perf_counter()
    run_id = ""
    revision: int | None = None
    confirmation_token: str | None = None
    turn_reports: list[dict[str, Any]] = []
    trace: dict[str, Any] | None = None
    try:
        for index, turn in enumerate(scenario.turns):
            trace_id = f"trc_scenario_{scenario.case_id}_{index}_{uuid.uuid4().hex}"
            if index == 0:
                current_run_id, events, latency_ms = request_sse(
                    f"{context.public_base}/api/v1/agent/runs/stream",
                    headers={**context.headers, "X-Trace-Id": trace_id},
                    body={
                        "threadId": None,
                        "message": turn.message,
                        "clientRequestId": str(uuid.uuid4()),
                    },
                )
                run_id = current_run_id
                require(bool(run_id), "start response has no runId")
                require(events[0][0] == "run.started", "new run did not start with run.started")
            else:
                require(revision is not None, "continuation has no expected revision")
                current_run_id, events, latency_ms = request_sse(
                    f"{context.public_base}/api/v1/agent/runs/{quote(run_id, safe='')}/input",
                    headers={**context.headers, "X-Trace-Id": trace_id},
                    body={
                        "message": turn.message,
                        "clientRequestId": str(uuid.uuid4()),
                        "expectedRevision": revision,
                    },
                )
                require(current_run_id == run_id, "continuation changed runId")
                require(
                    events[0][0] == "run.resumed", "continuation did not start with run.resumed"
                )

            actual_terminal = terminal_of(events)
            turn_report = event_summary(events)
            turn_report.update(
                {"turn": index + 1, "message": turn.message, "latencyMs": latency_ms}
            )
            turn_reports.append(turn_report)
            require(
                actual_terminal == turn.expected_terminal,
                f"turn {index + 1} expected {turn.expected_terminal}, got {actual_terminal}",
            )
            event_names = [name for name, _ in events]
            for required_event in turn.required_events:
                require(required_event in event_names, f"missing event {required_event}")
            requirement = latest_event(events, "requirement.updated")
            if requirement is not None and isinstance(requirement.get("revision"), int):
                revision = int(requirement["revision"])
            if turn.expected_terminal == "WAITING_INPUT":
                require(revision is not None, "waiting-input turn exposed no requirement revision")
            if turn.expected_terminal == "HITL":
                hitl = latest_event(events, "hitl.required")
                require(hitl is not None, "HITL terminal has no hitl.required data")
                token = hitl.get("confirmationToken")
                require(isinstance(token, str) and token, "HITL has no confirmation token")
                confirmation_token = token
                if scenario.expected_action is not None:
                    require(
                        hitl.get("actionType") == scenario.expected_action,
                        f"expected {scenario.expected_action} draft, got {hitl.get('actionType')}",
                    )
        require(bool(run_id), "scenario has no runId")
        if confirmation_token is not None:
            reject_draft(context, run_id, confirmation_token)

        trace, trace_headers = request_json(
            "GET",
            f"{context.public_base}/api/v1/agent/runs/{quote(run_id, safe='')}/trace",
            headers=context.headers,
        )
        cache_control = trace_headers.get("Cache-Control", trace_headers.get("Cache-control", ""))
        require("no-store" in cache_control.lower(), "trace response is cacheable")
        serialized_trace = json.dumps(trace, ensure_ascii=False)
        if confirmation_token is not None:
            require(confirmation_token not in serialized_trace, "trace leaked a confirmation token")
        run = trace.get("run")
        require(isinstance(run, dict), "trace lacks run metadata")
        require(run.get("intent") == scenario.expected_intent, f"intent is {run.get('intent')}")
        tool_calls = trace.get("toolCalls")
        require(isinstance(tool_calls, list), "trace lacks tool calls")
        tool_names = [item.get("toolName") for item in tool_calls if isinstance(item, dict)]
        for required_tool in scenario.required_tools:
            require(required_tool in tool_names, f"missing tool {required_tool}")
        require(
            not any(str(name).startswith("confirm_") for name in tool_names),
            "reject-only scenario executed a confirm tool",
        )
        citation_ids = turn_reports[-1]["citationIds"]
        if scenario.allowed_citations is not None or scenario.allowed_citation_prefixes:
            require(bool(citation_ids), "grounded policy answer returned no citation")
            require(
                all(
                    isinstance(item, str)
                    and (
                        item in (scenario.allowed_citations or ())
                        or any(
                            item.startswith(prefix)
                            for prefix in scenario.allowed_citation_prefixes
                        )
                    )
                    for item in citation_ids
                ),
                f"policy returned unrelated citations {citation_ids}",
            )
        if scenario.require_no_citations:
            require(not citation_ids, f"unknown policy answer returned citations {citation_ids}")
        for meeting_id in scenario.protected_meeting_ids:
            assert_meeting_unchanged(context, meeting_id)
        return {
            "caseId": scenario.case_id,
            "purpose": scenario.purpose,
            "status": "PASS",
            "latencyMs": round((time.perf_counter() - started) * 1000, 2),
            "runId": run_id,
            "turns": turn_reports,
            "observed": {
                "intent": run.get("intent"),
                "runStatus": run.get("status"),
                "provider": run.get("modelProvider"),
                "configuredModel": run.get("configuredModel"),
                "responseModels": run.get("responseModels", []),
                "promptVersion": run.get("promptVersion"),
                "schemaVersion": run.get("schemaVersion"),
                "modelCallCount": run.get("modelCallCount"),
                "toolCallCount": run.get("toolCallCount"),
                "tools": tool_names,
            },
        }
    except (KeyError, OSError, ScenarioFailure, TypeError, ValueError) as exc:
        if confirmation_token and run_id:
            with suppress(Exception):
                reject_draft(context, run_id, confirmation_token)
        for meeting_id in scenario.protected_meeting_ids:
            with suppress(Exception):
                assert_meeting_unchanged(context, meeting_id)
        return {
            "caseId": scenario.case_id,
            "purpose": scenario.purpose,
            "status": "FAIL",
            "latencyMs": round((time.perf_counter() - started) * 1000, 2),
            "runId": run_id or None,
            "turns": turn_reports,
            "failure": safe_error(exc),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run public-API product scenarios.")
    parser.add_argument("--public-base", default="http://localhost")
    parser.add_argument("--username", default="zhangsan")
    parser.add_argument("--password", default="demo-password")
    parser.add_argument(
        "--case",
        action="append",
        dest="case_ids",
        help="Run only the named case; may be repeated.",
    )
    parser.add_argument("--scenario-delay-seconds", type=float, default=1.0)
    parser.add_argument("--transient-retries", type=int, default=1)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/product-scenario-evaluation.json"),
    )
    args = parser.parse_args()
    public_base = args.public_base.rstrip("/")
    headers = login(public_base, args.username, args.password)
    _, owned = list_owned_meetings(public_base, headers)
    scenarios = build_scenarios(owned)
    if args.case_ids:
        requested = set(args.case_ids)
        scenarios = tuple(item for item in scenarios if item.case_id in requested)
        missing = requested.difference(item.case_id for item in scenarios)
        if missing:
            raise ScenarioFailure("unknown case IDs: " + ", ".join(sorted(missing)))
    protected_ids = sorted(
        {meeting_id for scenario in scenarios for meeting_id in scenario.protected_meeting_ids}
    )
    protected_before = {
        meeting_id: meeting_snapshot(public_base, headers, meeting_id)
        for meeting_id in protected_ids
    }
    context = ScenarioContext(
        public_base=public_base,
        headers=headers,
        protected_before=protected_before,
    )
    for scenario in scenarios:
        result = execute_scenario(context, scenario)
        retry_count = 0
        while (
            result["status"] == "FAIL"
            and "MODEL_UNAVAILABLE" in str(result.get("failure", ""))
            and retry_count < max(0, args.transient_retries)
        ):
            retry_count += 1
            time.sleep(5 * retry_count)
            result = execute_scenario(context, scenario)
        if retry_count:
            result["transientRetryCount"] = retry_count
        context.results.append(result)
        print(
            json.dumps(
                {
                    "caseId": result["caseId"],
                    "status": result["status"],
                    "failure": result.get("failure"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        if args.scenario_delay_seconds > 0:
            time.sleep(args.scenario_delay_seconds)

    passed = sum(item["status"] == "PASS" for item in context.results)
    total = len(context.results)
    latencies = sorted(float(item["latencyMs"]) for item in context.results)
    report = {
        "schemaVersion": "product-scenario-evaluation-v1",
        "mode": "public-api-adversarial-dialogue",
        "generatedAt": datetime.now(UTC).isoformat(),
        "status": "PASS" if passed == total else "FAIL",
        "safety": {
            "confirmationPolicy": "All HITL drafts are rejected",
            "protectedMeetingSnapshots": protected_before,
            "secretsPersisted": False,
        },
        "metrics": {
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "successRate": passed / total if total else 0.0,
            "latencyP50Ms": latencies[len(latencies) // 2] if latencies else 0.0,
            "latencyP95Ms": latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))]
            if latencies
            else 0.0,
        },
        "results": context.results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))
    return 0 if passed == total else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyError, OSError, ScenarioFailure, TypeError, ValueError) as exc:
        print(f"scenario evaluation failed: {safe_error(exc)}", file=sys.stderr)
        raise SystemExit(1) from exc
