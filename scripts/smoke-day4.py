#!/usr/bin/env python3
"""Day 4 real-stack smoke for the Java-proxied deterministic Agent stream."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
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
        with urlopen(request, timeout=30) as response:
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
        with urlopen(request, timeout=60) as response:
            run_id = response.headers.get("X-Run-Id", "")
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        raise SmokeFailure(f"agent stream returned HTTP {exc.code}: {body_text}") from exc

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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-base", default="http://localhost")
    parser.add_argument("--agent-base", default="http://localhost:8000")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()

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
    trace_id = f"trc_day4_{uuid.uuid4().hex}"
    run_id, events = request_sse(
        f"{public_base}/api/v1/agent/runs/stream",
        headers={
            "Authorization": f"Bearer {access_token}",
            "X-Trace-Id": trace_id,
        },
        body={
            "threadId": None,
            "message": "下周三下午帮张三和李四安排一个90分钟架构评审，要大屏",
            "clientRequestId": str(uuid.uuid4()),
        },
    )

    require(bool(run_id), "Java proxy did not return X-Run-Id")
    require(len(events) >= 2, "agent stream has too few events")
    require(events[0][0] == "run.started", "run.started is not the first SSE event")
    require(events[-1][0] == "run.completed", "run.completed is not the terminal SSE event")
    require(events[-1][1].get("status") == "SUCCEEDED", "agent run did not succeed")
    require(
        all(event_data.get("runId") == run_id for _, event_data in events),
        "an SSE event has a different runId than Java's X-Run-Id",
    )

    step_agents = {
        str(event_data.get("agentName"))
        for event_name, event_data in events
        if event_name == "agent.step"
    }
    require(
        {"supervisor", "requirement", "scheduling"}.issubset(step_agents),
        f"expected supervisor/requirement/scheduling steps, got {sorted(step_agents)}",
    )
    tool_events = [event_data for event_name, event_data in events if event_name == "tool.call"]
    require(
        any(
            event.get("toolName") == "resolve_employees"
            and event.get("riskLevel") == "READ"
            and event.get("status") == "SUCCEEDED"
            for event in tool_events
        ),
        "fixture stream did not complete a successful resolve_employees READ Tool call",
    )

    agent_context = issue_agent_context_token(
        secret=context_secret, user_id=user_id, trace_id=trace_id, run_id=run_id
    )
    _, trace, _ = request_json(
        "GET",
        f"{agent_base}/internal/v1/agent-runs/{run_id}/trace",
        headers={
            "Authorization": f"Bearer {agent_context}",
            "X-Service-Token": service_token,
            "X-Trace-Id": trace_id,
            "X-Run-Id": run_id,
        },
    )
    run = trace.get("run", {})
    require(run.get("runId") == run_id, "persisted trace runId does not match stream")
    require(run.get("status") == "SUCCEEDED", "persisted run is not SUCCEEDED")
    require(int(run.get("toolCallCount", 0)) >= 1, "run metadata has no persisted Tool call")
    persisted_steps = trace.get("steps", [])
    persisted_tools = trace.get("toolCalls", [])
    require(len(persisted_steps) >= 3, "agent_step metadata was not persisted")
    require(
        any(tool.get("toolName") == "resolve_employees" for tool in persisted_tools),
        "agent_tool_call metadata was not persisted",
    )

    policy_trace_id = f"trc_day4_policy_{uuid.uuid4().hex}"
    policy_run_id, policy_events = request_sse(
        f"{public_base}/api/v1/agent/runs/stream",
        headers={
            "Authorization": f"Bearer {access_token}",
            "X-Trace-Id": policy_trace_id,
        },
        body={
            "threadId": None,
            "message": "VIP会议室有什么使用规则？",
            "clientRequestId": str(uuid.uuid4()),
        },
    )
    require(bool(policy_run_id), "policy stream did not receive X-Run-Id")
    require(
        policy_events and policy_events[-1][0] == "run.completed",
        "policy stream did not complete",
    )
    policy_step_agents = {
        str(event_data.get("agentName"))
        for event_name, event_data in policy_events
        if event_name == "agent.step"
    }
    require(
        {"supervisor", "policy"}.issubset(policy_step_agents),
        f"policy request was not routed through the policy Agent: {sorted(policy_step_agents)}",
    )
    citations = policy_events[-1][1].get("citations", [])
    require(isinstance(citations, list) and citations, "policy response has no citation")
    require(
        all(
            isinstance(citation, dict)
            and citation.get("chunkId")
            and citation.get("title")
            and citation.get("headingPath")
            for citation in citations
        ),
        "policy citation is not verifiable source metadata",
    )

    print(
        json.dumps(
            {
                "javaSseProxy": "PASS",
                "runId": run_id,
                "traceId": trace_id,
                "eventTypes": [event_name for event_name, _ in events],
                "persistedSteps": len(persisted_steps),
                "persistedToolCalls": len(persisted_tools),
                "policyRunId": policy_run_id,
                "policyCitationCount": len(citations),
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
        print(f"Day 4 smoke failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
