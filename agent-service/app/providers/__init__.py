"""Configured model-provider factory."""

from __future__ import annotations

from datetime import datetime

from app.config import Settings
from app.providers.base import ModelProvider
from app.providers.deepseek import DeepSeekModelProvider
from app.providers.fixture import FixtureModelProvider


def build_model_provider(settings: Settings) -> ModelProvider:
    if settings.agent_model_provider == "deepseek":
        return DeepSeekModelProvider(settings)
    return FixtureModelProvider(datetime.fromisoformat(settings.fixture_now))


__all__ = ["ModelProvider", "build_model_provider"]
