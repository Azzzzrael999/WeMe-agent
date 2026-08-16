"""Shared errors, event buffering, and model accounting helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.providers.base import (
    ModelCompletion,
    ModelOutputError,
    ModelProvider,
    ModelProviderError,
    ModelRequest,
    StructuredModelRunner,
)
from app.schemas.agent import (
    AgentState,
)


class WorkflowError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class EventSink:
    _events: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    def emit(self, event_name: str, data: dict[str, object]) -> None:
        self._events.append((event_name, data))

    def drain(self) -> list[tuple[str, dict[str, object]]]:
        events = self._events[:]
        self._events.clear()
        return events


def _model_output_with_count(
    *,
    provider: ModelProvider,
    runner: StructuredModelRunner,
    agent_name: str,
    system_prompt: str,
    user_prompt: str,
    output_type: type[Any],
) -> tuple[Any, list[ModelCompletion]]:
    try:
        return runner.invoke_with_count(
            provider=provider,
            request=ModelRequest(
                agent_name=agent_name,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema_name=output_type.__name__,
                schema=output_type.model_json_schema(by_alias=True),
            ),
            output_type=output_type,
        )
    except ModelOutputError as exc:
        raise WorkflowError("MODEL_OUTPUT_INVALID", "模型结构化输出校验失败") from exc
    except ModelProviderError as exc:
        raise WorkflowError("MODEL_UNAVAILABLE", "模型服务暂不可用") from exc


def _apply_completions(state: AgentState, completions: list[ModelCompletion]) -> AgentState:
    response_models = list(state.response_models)
    for completion in completions:
        if completion.model and completion.model not in response_models:
            response_models.append(completion.model)
    return state.model_copy(
        update={
            "response_models": response_models[:12],
            "input_tokens": state.input_tokens
            + sum(item.usage.input_tokens for item in completions),
            "output_tokens": state.output_tokens
            + sum(item.usage.output_tokens for item in completions),
            "cache_hit_tokens": state.cache_hit_tokens
            + sum(item.usage.cache_hit_tokens for item in completions),
            "cache_miss_tokens": state.cache_miss_tokens
            + sum(item.usage.cache_miss_tokens for item in completions),
        }
    )
