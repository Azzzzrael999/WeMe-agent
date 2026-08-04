"""OpenAI-compatible model provider boundary and structured-output guard."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel, ValidationError


class ModelProviderError(RuntimeError):
    """Raised after the provider has exhausted its bounded network retries."""


class ModelOutputError(RuntimeError):
    """Raised after one bounded repair attempt still fails schema validation."""


@dataclass(frozen=True)
class ModelRequest:
    agent_name: str
    system_prompt: str
    user_prompt: str
    schema_name: str
    schema: dict[str, object]
    repair_attempt: int = 0


@dataclass(frozen=True)
class ModelUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0


@dataclass(frozen=True)
class ModelCompletion:
    content: str | None
    tool_calls: tuple[ModelToolCall, ...] = field(default_factory=tuple)
    usage: ModelUsage = field(default_factory=ModelUsage)
    model: str | None = None


@dataclass(frozen=True)
class ToolDefinition:
    """One strict OpenAI-compatible function exposed to the model."""

    name: str
    description: str
    parameters: dict[str, object]


@dataclass(frozen=True)
class ToolLoopMessage:
    """Provider-neutral subset of OpenAI chat messages used by the READ loop."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str | None
    tool_call_id: str | None = None
    tool_calls: tuple[ModelToolCall, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ModelToolCall:
    """A model correlation ID plus untrusted function arguments."""

    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class ToolModelRequest:
    agent_name: str
    messages: tuple[ToolLoopMessage, ...]
    tools: tuple[ToolDefinition, ...]
    iteration: int


@dataclass(frozen=True)
class ToolModelResponse:
    content: str | None
    tool_calls: tuple[ModelToolCall, ...]
    usage: ModelUsage = field(default_factory=ModelUsage)
    model: str | None = None


class ModelProvider(Protocol):
    """Small replaceable port for fixture and DeepSeek implementations."""

    def complete(self, request: ModelRequest) -> ModelCompletion | str:
        """Return one JSON object plus provider-reported usage/model."""

    def complete_tools(self, request: ToolModelRequest) -> ToolModelResponse:
        """Return native ``assistant.tool_calls`` for a bounded READ-only loop."""


ModelT = TypeVar("ModelT", bound=BaseModel)


class StructuredModelRunner:
    """Validates every model result and allows exactly one repair request."""

    def invoke(
        self,
        *,
        provider: ModelProvider,
        request: ModelRequest,
        output_type: type[ModelT],
    ) -> ModelT:
        result, _ = self.invoke_with_count(
            provider=provider, request=request, output_type=output_type
        )
        return result

    def invoke_with_count(
        self,
        *,
        provider: ModelProvider,
        request: ModelRequest,
        output_type: type[ModelT],
    ) -> tuple[ModelT, list[ModelCompletion]]:
        """Return validated output and every provider completion (including repair)."""

        completion_value = provider.complete(request)
        completion = (
            completion_value
            if isinstance(completion_value, ModelCompletion)
            else ModelCompletion(content=completion_value)
        )
        raw = completion.content
        if raw is None:
            raise ModelOutputError(f"{request.agent_name} returned empty structured content")
        try:
            return output_type.model_validate_json(raw), [completion]
        except ValidationError as first_error:
            repair_request = ModelRequest(
                agent_name=request.agent_name,
                system_prompt=request.system_prompt,
                user_prompt=(
                    f"{request.user_prompt}\n\n"
                    "The previous response did not satisfy the required JSON schema. "
                    "Return only a corrected JSON object; do not include an explanation. "
                    f"Validation summary: {first_error.error_count()} invalid field(s)."
                ),
                schema_name=request.schema_name,
                schema=request.schema,
                repair_attempt=1,
            )
            repaired_value = provider.complete(repair_request)
            repaired = (
                repaired_value
                if isinstance(repaired_value, ModelCompletion)
                else ModelCompletion(content=repaired_value)
            )
            repaired_content = repaired.content
            if repaired_content is None:
                raise ModelOutputError(
                    f"{request.agent_name} returned empty structured content after repair"
                ) from first_error
            try:
                return output_type.model_validate_json(repaired_content), [completion, repaired]
            except ValidationError as repair_error:
                raise ModelOutputError(
                    f"{request.agent_name} returned invalid {request.schema_name} after repair"
                ) from repair_error
