"""The real DeepSeek OpenAI-compatible provider, configured only by Settings."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings
from app.providers.base import ModelProviderError, ModelRequest


@dataclass
class DeepSeekModelProvider:
    settings: Settings
    client: httpx.Client | None = None

    def complete(self, request: ModelRequest) -> str:
        api_key = self.settings.deepseek_api_key.get_secret_value().strip()
        if not self.settings.deepseek_is_configured or not api_key:
            raise ModelProviderError("DEEPSEEK is not configured")

        payload: dict[str, Any] = {
            "model": self.settings.deepseek_model,
            "temperature": 0,
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
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        owned_client = self.client is None
        client = self.client or httpx.Client(timeout=self.settings.model_timeout_seconds)
        try:
            for attempt in range(self.settings.model_max_retries + 1):
                try:
                    response = client.post(
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
                    return self._content(response.json())
                except httpx.RequestError as exc:
                    if attempt < self.settings.model_max_retries:
                        time.sleep(0.1 * (2**attempt))
                        continue
                    raise ModelProviderError("DeepSeek network failure") from exc
        finally:
            if owned_client:
                client.close()
        raise ModelProviderError("DeepSeek retry loop ended unexpectedly")

    @staticmethod
    def _content(payload: object) -> str:
        if not isinstance(payload, dict):
            raise ModelProviderError("DeepSeek response is invalid")
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ModelProviderError("DeepSeek response has no choices")
        first = choices[0]
        if not isinstance(first, dict):
            raise ModelProviderError("DeepSeek response choice is invalid")
        message = first.get("message")
        if not isinstance(message, dict):
            raise ModelProviderError("DeepSeek response message is invalid")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ModelProviderError("DeepSeek response content is empty")
        return content
