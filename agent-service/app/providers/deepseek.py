"""The real DeepSeek OpenAI-compatible provider, configured only by Settings."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings
from app.providers.base import (
    ModelCompletion,
    ModelProviderError,
    ModelRequest,
    ModelToolCall,
    ModelUsage,
    ToolLoopMessage,
    ToolModelRequest,
    ToolModelResponse,
)


@dataclass
class DeepSeekModelProvider:
    settings: Settings
    client: httpx.Client | None = None

    def complete(self, request: ModelRequest) -> ModelCompletion:
        payload: dict[str, Any] = {
            "model": self.settings.deepseek_model,
            "temperature": 0,
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"{request.system_prompt}\n\n"
                        "Return only one JSON object that validates against this schema: "
                        f"{json.dumps(request.schema, ensure_ascii=False, separators=(',', ':'))}"
                    ),
                },
                {"role": "user", "content": request.user_prompt},
            ],
        }
        response = self._post(payload)
        return ModelCompletion(
            content=self._content(response),
            usage=self._usage(response),
            model=self._model(response),
        )

    def complete_tools(self, request: ToolModelRequest) -> ToolModelResponse:
        """Call DeepSeek's OpenAI-compatible native function-calling API."""

        payload: dict[str, Any] = {
            "model": self.settings.deepseek_model,
            "temperature": 0,
            "thinking": {"type": "disabled"},
            "messages": [self._tool_message(message) for message in request.messages],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
                for tool in request.tools
            ],
            "tool_choice": "auto",
        }
        response = self._post(payload)
        return self._tool_response(response)

    def _post(self, payload: dict[str, Any]) -> object:
        api_key = self.settings.deepseek_api_key.get_secret_value().strip()
        if not self.settings.deepseek_is_configured or not api_key:
            raise ModelProviderError("DEEPSEEK is not configured")
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        if self.client is None:
            self.client = httpx.Client(
                timeout=self.settings.model_timeout_seconds,
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
        for attempt in range(self.settings.model_max_retries + 1):
            try:
                response = self.client.post(
                    self.settings.deepseek_base_url.rstrip("/") + "/chat/completions",
                    headers=headers,
                    json=payload,
                )
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < self.settings.model_max_retries:
                        time.sleep(0.1 * (2**attempt))
                        continue
                    raise ModelProviderError("DeepSeek temporary failure")
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                raise ModelProviderError("DeepSeek request rejected") from exc
            except httpx.RequestError as exc:
                if attempt < self.settings.model_max_retries:
                    time.sleep(0.1 * (2**attempt))
                    continue
                raise ModelProviderError("DeepSeek network failure") from exc
        raise ModelProviderError("DeepSeek retry loop ended unexpectedly")

    @staticmethod
    def _tool_message(message: ToolLoopMessage) -> dict[str, Any]:
        value: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.tool_call_id is not None:
            value["tool_call_id"] = message.tool_call_id
        if message.tool_calls:
            value["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments},
                }
                for call in message.tool_calls
            ]
        return value

    @staticmethod
    def _tool_response(payload: object) -> ToolModelResponse:
        message = DeepSeekModelProvider._message(payload)
        raw_calls = message.get("tool_calls", [])
        if not isinstance(raw_calls, list) or len(raw_calls) > 8:
            raise ModelProviderError("DeepSeek tool_calls is invalid")
        calls: list[ModelToolCall] = []
        for raw in raw_calls:
            if (
                not isinstance(raw, dict)
                or not isinstance(raw.get("id"), str)
                or not 1 <= len(raw["id"]) <= 128
            ):
                raise ModelProviderError("DeepSeek tool call is invalid")
            function = raw.get("function")
            if not isinstance(function, dict):
                raise ModelProviderError("DeepSeek tool function is invalid")
            name = function.get("name")
            arguments = function.get("arguments")
            if (
                not isinstance(name, str)
                or not 1 <= len(name) <= 64
                or not isinstance(arguments, str)
                or len(arguments.encode("utf-8")) > 8192
            ):
                raise ModelProviderError("DeepSeek tool function is invalid")
            calls.append(ModelToolCall(id=raw["id"], name=name, arguments=arguments))
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            raise ModelProviderError("DeepSeek response content is invalid")
        if not calls and (not isinstance(content, str) or not content.strip()):
            raise ModelProviderError("DeepSeek response has neither content nor tool calls")
        return ToolModelResponse(
            content=content,
            tool_calls=tuple(calls),
            usage=DeepSeekModelProvider._usage(payload),
            model=DeepSeekModelProvider._model(payload),
        )

    @staticmethod
    def _usage(payload: object) -> ModelUsage:
        usage = payload.get("usage", {}) if isinstance(payload, dict) else {}
        if not isinstance(usage, dict):
            return ModelUsage()
        return ModelUsage(
            input_tokens=_safe_token(usage.get("prompt_tokens")),
            output_tokens=_safe_token(usage.get("completion_tokens")),
            cache_hit_tokens=_safe_token(usage.get("prompt_cache_hit_tokens")),
            cache_miss_tokens=_safe_token(usage.get("prompt_cache_miss_tokens")),
        )

    @staticmethod
    def _model(payload: object) -> str | None:
        if not isinstance(payload, dict):
            return None
        model = payload.get("model")
        return model[:128] if isinstance(model, str) and model else None

    @staticmethod
    def _message(payload: object) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ModelProviderError("DeepSeek response is invalid")
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ModelProviderError("DeepSeek response has no choices")
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise ModelProviderError("DeepSeek response message is invalid")
        return message

    @staticmethod
    def _content(payload: object) -> str:
        message = DeepSeekModelProvider._message(payload)
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ModelProviderError("DeepSeek response content is empty")
        return content


def _safe_token(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0
