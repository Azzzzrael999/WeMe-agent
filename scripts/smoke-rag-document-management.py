#!/usr/bin/env python3
"""Public-API smoke test for managed RAG meeting-policy documents.

The script uses only Java's browser-facing `/api/v1/**` surface. It restores a
fixed fictional Markdown document when necessary, verifies employee read-only
access and administrator CRUD, then deletes the smoke document. The DELETED
tombstone deliberately remains so a later `rag-init` run can prove that seed
ingestion does not resurrect an explicitly deleted document.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DOCUMENT_ID = "doc_rag_management_smoke"
FILE_NAME = "rag-management-smoke.md"


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
    payload: bytes | None = None,
    content_type: str | None = None,
) -> tuple[int, dict[str, Any]]:
    actual_headers = {"Accept": "application/json", **(headers or {})}
    actual_payload = payload
    if body is not None:
        actual_payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
        actual_headers["Content-Type"] = "application/json; charset=utf-8"
    elif content_type is not None:
        actual_headers["Content-Type"] = content_type
    request = Request(url, data=actual_payload, headers=actual_headers, method=method)
    try:
        with urlopen(request, timeout=90) as response:
            raw = response.read().decode("utf-8")
            parsed = json.loads(raw) if raw else {}
            require(isinstance(parsed, dict), f"{method} {url} returned non-object JSON")
            return response.status, parsed
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"raw": raw}
        require(isinstance(parsed, dict), f"{method} {url} returned a non-object error")
        return exc.code, parsed


def request_ok(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    payload: bytes | None = None,
    content_type: str | None = None,
) -> dict[str, Any]:
    status, response = request_raw(
        method,
        url,
        headers=headers,
        body=body,
        payload=payload,
        content_type=content_type,
    )
    if not 200 <= status < 300:
        raise SmokeFailure(f"{method} {url} returned HTTP {status}: {response}")
    data = response.get("data")
    require(isinstance(data, dict), f"{method} {url} has no object data envelope")
    return data


def login(public_base: str, username: str) -> dict[str, str]:
    data = request_ok(
        "POST",
        f"{public_base}/api/v1/auth/login",
        body={"username": username, "password": "demo-password"},
    )
    token = data.get("accessToken")
    require(isinstance(token, str) and bool(token), f"{username} login returned no token")
    return {"Authorization": f"Bearer {token}"}


def markdown(title: str, body: str) -> bytes:
    return (
        "---\n"
        f"documentId: {DOCUMENT_ID}\n"
        f"title: {title}\n"
        "documentType: MEETING_POLICY\n"
        "department: ALL\n"
        'version: "1.0"\n'
        'effectiveDate: "2026-08-15"\n'
        "status: ACTIVE\n"
        "priority: 10\n"
        "timezone: Asia/Shanghai\n"
        "---\n\n"
        f"# {title}\n\n{body}\n"
    ).encode()


def multipart_file(file_content: bytes) -> tuple[bytes, str]:
    boundary = f"----weme-{uuid.uuid4().hex}"
    prefix = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{FILE_NAME}"\r\n'
        "Content-Type: text/markdown\r\n\r\n"
    ).encode("ascii")
    payload = prefix + file_content + f"\r\n--{boundary}--\r\n".encode("ascii")
    return payload, f"multipart/form-data; boundary={boundary}"


def upload(public_base: str, headers: dict[str, str], content: bytes) -> dict[str, Any]:
    payload, content_type = multipart_file(content)
    return request_ok(
        "POST",
        f"{public_base}/api/v1/admin/knowledge-documents",
        headers=headers,
        payload=payload,
        content_type=content_type,
    )


def verify_deleted(public_base: str, headers: dict[str, str]) -> None:
    status, response = request_raw(
        "GET",
        f"{public_base}/api/v1/knowledge-documents/{DOCUMENT_ID}",
        headers=headers,
    )
    require(
        status == 404 and response.get("code") == "RAG_DOCUMENT_NOT_FOUND",
        f"deleted smoke document is visible: HTTP {status} {response}",
    )


def run(public_base: str, *, verify_tombstone_only: bool) -> None:
    employee_headers = login(public_base, "zhangsan")
    if verify_tombstone_only:
        verify_deleted(public_base, employee_headers)
        print(json.dumps({"status": "PASS", "tombstonePreserved": True, "documentId": DOCUMENT_ID}))
        return

    admin_headers = login(public_base, "admin")
    listed = request_ok(
        "GET",
        f"{public_base}/api/v1/knowledge-documents?{urlencode({'page': 1, 'size': 100})}",
        headers=employee_headers,
    )
    items = listed.get("items")
    require(isinstance(items, list) and len(items) >= 22, "seeded RAG documents are not browsable")

    employee_payload, employee_content_type = multipart_file(
        markdown("Employee forbidden smoke", "This upload must be rejected.")
    )
    forbidden_status, forbidden = request_raw(
        "POST",
        f"{public_base}/api/v1/admin/knowledge-documents",
        headers=employee_headers,
        payload=employee_payload,
        content_type=employee_content_type,
    )
    require(
        forbidden_status == 403 and forbidden.get("code") == "FORBIDDEN",
        f"employee upload was not forbidden: HTTP {forbidden_status} {forbidden}",
    )

    # A prior successful run leaves a tombstone; explicit admin upload restores it.
    created = upload(
        public_base,
        admin_headers,
        markdown("RAG management smoke policy", "Initial fictional management rule."),
    )
    require(created.get("documentId") == DOCUMENT_ID, "upload returned the wrong document")
    require(created.get("status") == "INDEXED" and created.get("chunkCount", 0) > 0, "upload was not indexed")
    created_version = created.get("recordVersion")
    require(isinstance(created_version, int), "upload returned no management version")

    detail = request_ok(
        "GET",
        f"{public_base}/api/v1/knowledge-documents/{DOCUMENT_ID}",
        headers=employee_headers,
    )
    require("Initial fictional management rule." in detail.get("content", ""), "detail omitted source content")

    updated_text = markdown(
        "RAG management smoke policy",
        "Updated fictional management rule with an explicit admin revision.",
    ).decode("utf-8")
    updated = request_ok(
        "PUT",
        f"{public_base}/api/v1/admin/knowledge-documents/{DOCUMENT_ID}",
        headers=admin_headers,
        body={"content": updated_text, "expectedVersion": created_version},
    )
    updated_version = updated.get("recordVersion")
    require(updated_version == created_version + 1, "edit did not increment the management version")
    require("explicit admin revision" in updated.get("content", ""), "edit did not reindex new source")

    deleted = request_ok(
        "DELETE",
        f"{public_base}/api/v1/admin/knowledge-documents/{DOCUMENT_ID}?expectedVersion={updated_version}",
        headers=admin_headers,
    )
    require(deleted.get("status") == "DELETED", "delete did not create a tombstone")
    verify_deleted(public_base, employee_headers)
    print(
        json.dumps(
            {
                "status": "PASS",
                "seededDocumentCount": len(items),
                "employeeRead": True,
                "employeeWriteForbidden": True,
                "adminUploadEditDelete": True,
                "deletedSmokeDocument": DOCUMENT_ID,
            }
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-base", default="http://localhost")
    parser.add_argument("--verify-tombstone-only", action="store_true")
    args = parser.parse_args()
    try:
        run(args.public_base.rstrip("/"), verify_tombstone_only=args.verify_tombstone_only)
    except Exception as exc:  # noqa: BLE001 - concise CLI boundary
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
