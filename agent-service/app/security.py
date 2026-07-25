"""Authentication for Java-to-Python internal calls.

The public browser never reaches this service.  Java signs the short-lived
Agent Context JWT and Python validates it before it reads any request body
identity or calls a Java Tool.
"""

from __future__ import annotations

from dataclasses import dataclass
from hmac import compare_digest
from typing import Any

import jwt

from app.config import Settings


class InternalAuthenticationError(ValueError):
    """A deliberately non-sensitive internal authentication failure."""


@dataclass(frozen=True)
class AgentContext:
    user_id: int
    roles: tuple[str, ...]
    trace_id: str
    run_id: str
    token: str

    @property
    def is_admin(self) -> bool:
        return "ADMIN" in self.roles


def authenticate_agent_context(
    *,
    settings: Settings,
    authorization: str | None,
    service_token: str | None,
    trace_id: str | None,
    run_id: str | None,
) -> AgentContext:
    if not authorization or not authorization.startswith("Bearer "):
        raise InternalAuthenticationError("agent context is required")
    if not service_token or not compare_digest(
        service_token, settings.internal_service_token.get_secret_value()
    ):
        raise InternalAuthenticationError("service token is invalid")
    if not trace_id or not run_id or len(trace_id) > 64 or len(run_id) > 64:
        raise InternalAuthenticationError("run context headers are invalid")

    token = authorization.removeprefix("Bearer ").strip()
    secret = settings.agent_context_jwt_secret.get_secret_value()
    if not token or not secret:
        raise InternalAuthenticationError("agent context is invalid")
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience=settings.agent_context_audience,
            options={"require": ["sub", "aud", "exp"]},
        )
    except jwt.PyJWTError as exc:
        raise InternalAuthenticationError("agent context is invalid") from exc

    try:
        user_id = int(claims["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise InternalAuthenticationError("agent context is invalid") from exc
    raw_roles = claims.get("roles")
    if (
        user_id < 1
        or not isinstance(raw_roles, list)
        or not raw_roles
        or any(not isinstance(role, str) or not role for role in raw_roles)
    ):
        raise InternalAuthenticationError("agent context is invalid")
    if claims.get("traceId") != trace_id or claims.get("runId") != run_id:
        raise InternalAuthenticationError("agent context headers do not match")
    return AgentContext(
        user_id=user_id,
        roles=tuple(raw_roles),
        trace_id=trace_id,
        run_id=run_id,
        token=token,
    )
