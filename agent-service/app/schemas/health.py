from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ServiceStatus(StrEnum):
    UP = "UP"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"


class ComponentStatus(StrEnum):
    UP = "UP"
    DOWN = "DOWN"
    CONFIGURED = "CONFIGURED"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    NOT_CHECKED = "NOT_CHECKED"


class HealthResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: ServiceStatus
    deepseek: ComponentStatus
    database: ComponentStatus
    redis_checkpoint: ComponentStatus = Field(serialization_alias="redisCheckpoint")
    qdrant: ComponentStatus
    business_service: ComponentStatus = Field(serialization_alias="businessService")
