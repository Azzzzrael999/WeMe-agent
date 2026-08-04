"""Real HTTP contention check for the Day 2 synchronous booking path."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import random
import statistics
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter
from datetime import datetime, time as datetime_time, timedelta, timezone
from typing import Any


SHANGHAI = timezone(timedelta(hours=8))


def request_json(
    method: str,
    url: str,
    *,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30,
) -> tuple[int, dict[str, Any]]:
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request_headers = {"Accept": "application/json", **(headers or {})}
    if data is not None:
        request_headers["Content-Type"] = "application/json; charset=utf-8"
    request = urllib.request.Request(
        url,
        data=data,
        headers=request_headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        payload = json.loads(error.read().decode("utf-8"))
        return error.code, payload


def percentile(values: list[float], percentile_value: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(percentile_value * len(ordered)) - 1)
    return round(ordered[index], 2)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost")
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument(
        "--mode", choices=("room", "idempotency"), default="room"
    )
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    status, login = request_json(
        "POST",
        f"{base_url}/api/v1/auth/login",
        body={"username": "zhangsan", "password": "demo-password"},
    )
    if status != 200:
        raise SystemExit(f"Login failed: HTTP {status}")
    token = login["data"]["accessToken"]
    auth_headers = {"Authorization": f"Bearer {token}"}

    random_source = random.SystemRandom()
    booking_date = datetime.now(SHANGHAI).date() + timedelta(
        days=random_source.randint(1, 13)
    )
    start_slot = random_source.randint(16, 39)
    start = datetime.combine(
        booking_date,
        datetime_time(hour=start_slot // 2, minute=(start_slot % 2) * 30),
        SHANGHAI,
    )
    end = start + timedelta(minutes=90)
    booking_request = {
        "title": f"Day 2 百请求{args.mode}竞争",
        "meetingType": "CONCURRENCY_TEST",
        "roomId": 101,
        "startAt": start.isoformat(timespec="seconds"),
        "endAt": end.isoformat(timespec="seconds"),
        "requiredParticipantIds": [],
        "optionalParticipantIds": [],
    }

    shared_idempotency_key = str(uuid.uuid4())

    def compete(_: int) -> dict[str, Any]:
        started = time.perf_counter()
        response_status, payload = request_json(
            "POST",
            f"{base_url}/api/v1/meetings",
            body=booking_request,
            headers={
                **auth_headers,
                "Idempotency-Key": (
                    shared_idempotency_key
                    if args.mode == "idempotency"
                    else str(uuid.uuid4())
                ),
            },
        )
        return {
            "status": response_status,
            "code": payload.get("code"),
            "meetingId": (payload.get("data") or {}).get("id"),
            "latencyMs": (time.perf_counter() - started) * 1000,
        }

    meeting_id: int | None = None
    try:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=args.workers
        ) as executor:
            results = list(executor.map(compete, range(args.requests)))

        successes = [result for result in results if result["status"] == 200]
        conflicts = [
            result
            for result in results
            if result["status"] == 409 and result["code"] == "BOOKING_CONFLICT"
        ]
        unexpected = [
            result
            for result in results
            if result not in successes and result not in conflicts
        ]
        expected_successes = args.requests if args.mode == "idempotency" else 1
        expected_conflicts = 0 if args.mode == "idempotency" else args.requests - 1
        unique_successful_meetings = {
            result["meetingId"] for result in successes
        }
        if (
            len(successes) != expected_successes
            or len(conflicts) != expected_conflicts
            or len(unique_successful_meetings) != 1
            or unexpected
        ):
            raise SystemExit(
                json.dumps(
                    {
                        "successCount": len(successes),
                        "conflictCount": len(conflicts),
                        "uniqueSuccessfulMeetingIds": len(
                            unique_successful_meetings
                        ),
                        "unexpected": Counter(
                            (result["status"], result["code"])
                            for result in unexpected
                        ),
                    },
                    ensure_ascii=False,
                    default=dict,
                )
            )

        meeting_id = successes[0]["meetingId"]
        latencies = [result["latencyMs"] for result in results]
        print(
            json.dumps(
                {
                    "requests": args.requests,
                    "mode": args.mode,
                    "successCount": len(successes),
                    "conflictCount": len(conflicts),
                    "uniqueSuccessfulMeetingIds": len(unique_successful_meetings),
                    "roomId": booking_request["roomId"],
                    "startAt": booking_request["startAt"],
                    "endAt": booking_request["endAt"],
                    "latencyMs": {
                        "mean": round(statistics.mean(latencies), 2),
                        "p50": percentile(latencies, 0.50),
                        "p95": percentile(latencies, 0.95),
                        "p99": percentile(latencies, 0.99),
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        if meeting_id is not None:
            request_json(
                "DELETE",
                f"{base_url}/api/v1/meetings/{meeting_id}",
                headers=auth_headers,
            )


if __name__ == "__main__":
    main()
