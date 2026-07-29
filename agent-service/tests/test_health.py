from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.internal import get_checkpoint_saver
from app.config import get_settings
from app.database.health import probe_database
from app.main import app
from app.schemas.health import ComponentStatus


class FakeCheckpointSaver:
    def __init__(self, available: bool) -> None:
        self.available = available

    def probe(self) -> bool:
        return self.available


@pytest.fixture(autouse=True)
def clear_dependency_state() -> Iterator[None]:
    app.dependency_overrides.clear()
    get_settings.cache_clear()
    yield
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def test_health_is_degraded_without_deepseek_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    app.dependency_overrides[get_checkpoint_saver] = lambda: FakeCheckpointSaver(True)

    with TestClient(app) as client:
        response = client.get("/internal/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "DEGRADED",
        "deepseek": "NOT_CONFIGURED",
        "database": "UP",
        "redisCheckpoint": "UP",
        "qdrant": "NOT_CHECKED",
        "businessService": "NOT_CHECKED",
    }


def test_health_does_not_call_deepseek_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-placeholder-key")
    app.dependency_overrides[get_checkpoint_saver] = lambda: FakeCheckpointSaver(True)

    with TestClient(app) as client:
        response = client.get("/internal/v1/health")

    assert response.status_code == 200
    assert response.json()["status"] == "UP"
    assert response.json()["deepseek"] == "CONFIGURED"


def test_database_failure_makes_container_health_fail() -> None:
    app.dependency_overrides[probe_database] = lambda: ComponentStatus.DOWN
    app.dependency_overrides[get_checkpoint_saver] = lambda: FakeCheckpointSaver(True)

    with TestClient(app) as client:
        response = client.get("/internal/v1/health")

    assert response.status_code == 503
    assert response.json()["status"] == "DOWN"
    assert response.json()["database"] == "DOWN"


def test_checkpoint_failure_makes_container_health_fail() -> None:
    app.dependency_overrides[get_checkpoint_saver] = lambda: FakeCheckpointSaver(False)

    with TestClient(app) as client:
        response = client.get("/internal/v1/health")

    assert response.status_code == 503
    assert response.json()["status"] == "DOWN"
    assert response.json()["redisCheckpoint"] == "DOWN"
