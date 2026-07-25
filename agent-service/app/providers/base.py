"""OpenAI-compatible model provider boundary and structured-output guard."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeVar

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


class ModelProvider(Protocol):
    """Small replaceable port for fixture and DeepSeek implementations."""

    def complete(self, request: ModelRequest) -> str:
        """Return one JSON object string, never hidden reasoning."""


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
        raw = provider.complete(request)
        try:
            return output_type.model_validate_json(raw)
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
            repaired = provider.complete(repair_request)
            try:
                return output_type.model_validate_json(repaired)
            except ValidationError as repair_error:
                raise ModelOutputError(
                    f"{request.agent_name} returned invalid {request.schema_name} after repair"
                ) from repair_error
