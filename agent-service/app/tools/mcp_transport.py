"""Official MCP SDK bridge with one isolated client lifecycle per business call."""

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from datetime import timedelta
from functools import partial
from typing import Any

import anyio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import McpError
from mcp.types import CallToolResult, TextContent

from app.config import Settings
from app.tools.transport import JavaToolError, TransportResult, business_error

logger = logging.getLogger(__name__)


class LimitedStream(httpx.AsyncByteStream):
    def __init__(self, stream: httpx.AsyncByteStream, limit: int) -> None:
        self.stream = stream
        self.limit = limit

    async def __aiter__(self) -> AsyncIterator[bytes]:
        size = 0
        async for chunk in self.stream:
            size += len(chunk)
            if size > self.limit:
                raise JavaToolError("TOOL_RESPONSE_INVALID")
            yield chunk

    async def aclose(self) -> None:
        await self.stream.aclose()


class LimitedTransport(httpx.AsyncBaseTransport):
    def __init__(self, limit: int, inner: httpx.AsyncBaseTransport | None = None) -> None:
        self.inner = inner or httpx.AsyncHTTPTransport()
        self.limit = limit

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self.inner.handle_async_request(request)
        # Count actual JSON bytes; reject compression before HTTPX can inflate unbounded data.
        if response.headers.get("Content-Encoding", "identity").lower() != "identity":
            await response.aclose()
            raise JavaToolError("TOOL_RESPONSE_INVALID")
        length = response.headers.get("Content-Length")
        if length is not None and (not length.isdigit() or int(length) > self.limit):
            await response.aclose()
            raise JavaToolError("TOOL_RESPONSE_INVALID")
        if request.method == "POST" and response.status_code == 200:
            content_type = response.headers.get("Content-Type", "").split(";")[0].lower()
            if content_type not in {"application/json", "text/event-stream"}:
                await response.aclose()
                raise JavaToolError("TOOL_RESPONSE_INVALID")
        assert isinstance(response.stream, httpx.AsyncByteStream)
        response.stream = LimitedStream(response.stream, self.limit)
        return response

    async def aclose(self) -> None:
        await self.inner.aclose()


def parse_result(result: CallToolResult) -> dict[str, Any]:
    envelope = result.structuredContent
    # A single JSON text is the only supported compatibility representation.
    if not result.content and envelope is not None:
        text_envelope = envelope
    elif len(result.content) == 1 and isinstance(result.content[0], TextContent):
        try:
            text_envelope = json.loads(result.content[0].text)
        except ValueError as exc:
            raise JavaToolError("TOOL_RESPONSE_INVALID") from exc
    else:
        raise JavaToolError("TOOL_RESPONSE_INVALID")
    if envelope is None:
        envelope = text_envelope
    elif envelope != text_envelope:
        raise JavaToolError("TOOL_RESPONSE_INVALID")
    if not isinstance(envelope, dict) or type(envelope.get("ok")) is not bool:
        raise JavaToolError("TOOL_RESPONSE_INVALID")
    if envelope["ok"] is True:
        if result.isError or set(envelope) != {"ok", "data"}:
            raise JavaToolError("TOOL_RESPONSE_INVALID")
        data = envelope["data"]
        if not isinstance(data, dict):
            raise JavaToolError("TOOL_RESPONSE_INVALID")
        return data
    if not result.isError or set(envelope) != {"ok", "error"}:
        raise JavaToolError("TOOL_RESPONSE_INVALID")
    error = envelope["error"]
    if not isinstance(error, dict) or not isinstance(error.get("code"), str):
        raise JavaToolError("TOOL_RESPONSE_INVALID")
    raise business_error(error)


def normalize_error(exc: Exception) -> JavaToolError:
    if isinstance(exc, BaseExceptionGroup):
        errors = [
            normalize_error(child) for child in exc.exceptions if isinstance(child, Exception)
        ]
        # Preserve a definitive failure rather than retrying a parallel cleanup failure.
        for error in errors:
            if error.code not in {"TOOL_TIMEOUT", "TOOL_UNAVAILABLE"}:
                return error
        return errors[0] if errors else JavaToolError("TOOL_UNAVAILABLE")
    if isinstance(exc, JavaToolError):
        return exc
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in {401, 403}:
            return JavaToolError("TOOL_FORBIDDEN")
        return JavaToolError("TOOL_UNAVAILABLE" if status == 503 else "TOOL_PROTOCOL_ERROR")
    if isinstance(exc, TimeoutError | httpx.TimeoutException):
        return JavaToolError("TOOL_TIMEOUT")
    if isinstance(exc, McpError):
        if exc.error.code == 408:
            return JavaToolError("TOOL_TIMEOUT")
        return JavaToolError("TOOL_PROTOCOL_ERROR")
    if isinstance(exc, httpx.RequestError | anyio.EndOfStream | anyio.BrokenResourceError):
        return JavaToolError("TOOL_UNAVAILABLE")
    return JavaToolError("TOOL_RESPONSE_INVALID")


class McpToolTransport:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def call(
        self, name: str, arguments: dict[str, Any], headers: dict[str, str]
    ) -> TransportResult:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise JavaToolError("TOOL_SYNC_CONTEXT_INVALID")
        return anyio.run(partial(self._call, name, arguments, dict(headers)))

    async def _once(
        self, name: str, arguments: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]:
        failure: JavaToolError | None = None

        async def received(message: Any) -> None:
            nonlocal failure
            if isinstance(message, Exception):
                failure = normalize_error(message)
                # Stop the SDK receive loop so pending requests are released immediately.
                raise failure

        async with (
            httpx.AsyncClient(
                headers={**headers, "Accept-Encoding": "identity"},
                timeout=self.settings.agent_tool_timeout_seconds,
                follow_redirects=False,
                transport=LimitedTransport(self.settings.agent_tool_max_response_bytes),
            ) as client,
            streamable_http_client(self.settings.mcp_url, http_client=client) as streams,
            ClientSession(
                streams[0],
                streams[1],
                read_timeout_seconds=timedelta(seconds=self.settings.agent_tool_timeout_seconds),
                message_handler=received,
            ) as session,
        ):
            try:
                await session.initialize()
                return parse_result(await session.call_tool(name, arguments))
            except Exception as exc:
                if failure is not None:
                    raise failure from exc
                raise

    async def _call(
        self, name: str, arguments: dict[str, Any], headers: dict[str, str]
    ) -> TransportResult:
        started = time.monotonic()
        attempts = 0
        code = "SUCCESS"
        try:
            with anyio.fail_after(self.settings.agent_tool_timeout_seconds):
                for attempt in range(self.settings.agent_tool_max_retries + 1):
                    attempts = attempt + 1
                    try:
                        return TransportResult(
                            await self._once(name, arguments, headers), attempts=attempts
                        )
                    except Exception as exc:
                        error = normalize_error(exc)
                        if error.code not in {"TOOL_UNAVAILABLE", "TOOL_TIMEOUT"}:
                            raise error from exc
                        if attempt == self.settings.agent_tool_max_retries:
                            raise error from exc
                        await anyio.sleep(0.1 * 2**attempt)
        except Exception as exc:
            error = normalize_error(exc)
            code = error.code
            raise error from exc
        finally:
            logger.info(
                "business_tool transport=mcp tool=%s toolCallId=%s traceId=%s runId=%s "
                "attempts=%d duration_ms=%d code=%s",
                name,
                headers["X-Tool-Call-Id"],
                headers["X-Trace-Id"],
                headers["X-Run-Id"],
                attempts,
                int((time.monotonic() - started) * 1000),
                code,
            )
        raise JavaToolError("TOOL_UNAVAILABLE")
