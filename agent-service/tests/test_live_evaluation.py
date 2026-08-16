from datetime import datetime
from pathlib import Path

import pytest

from app.config import Settings
from app.evaluation.corpus import CASES, DATASET_VERSION
from app.evaluation.live import CORE_CASE_IDS, CORE_CASES, run_live_evaluation
from app.providers.fixture import FixtureModelProvider


def test_live_component_is_explicitly_skipped_without_provider_config() -> None:
    report = run_live_evaluation(
        mode="component",
        suite="core",
        repeats=3,
        settings=Settings(DEEPSEEK_API_KEY="", DEEPSEEK_BASE_URL="", DEEPSEEK_MODEL=""),
    )

    assert len(CORE_CASE_IDS) == 30
    assert len(CORE_CASES) == 30
    assert len(set(CORE_CASE_IDS)) == 30
    assert set(CORE_CASE_IDS).issubset({case.case_id for case in CASES})
    assert {case.expected_intent.value for case in CORE_CASES} == {
        "CREATE_MEETING",
        "QUERY_POLICY",
        "MODIFY_MEETING",
        "CANCEL_MEETING",
        "FIND_COMMON_TIME",
        "RECOMMEND_ROOM",
        "UPDATE_PREFERENCE",
    }
    assert {case.difficulty.value for case in CORE_CASES} == {"EASY", "MEDIUM", "HARD"}
    assert {case.split.value for case in CORE_CASES} == {"DEV", "VALIDATION", "HOLDOUT"}
    assert any("prompt-injection" in case.tags for case in CORE_CASES)
    assert any("rag" in case.tags for case in CORE_CASES)
    assert report["mode"] == "live-model-component"
    assert report["datasetVersion"] == DATASET_VERSION
    assert report["status"] == "SKIPPED"
    assert report["metrics"]["uniqueCases"] == 30
    assert report["metrics"]["samples"] == 0
    assert report["metrics"]["taskSuccessRate"] is None
    assert report["metrics"]["stableCaseRate"] is None
    assert report["results"] == []


def test_live_trajectory_never_claims_component_success() -> None:
    report = run_live_evaluation(mode="trajectory", suite="full", repeats=1)

    assert report["mode"] == "live-model-trajectory"
    assert report["status"] == "SKIPPED"
    assert "Java SSE" in str(report["reason"])


def test_live_component_reports_planned_tools_and_case_level_success_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.evaluation.live.DeepSeekModelProvider",
        lambda _settings: FixtureModelProvider(datetime.fromisoformat("2026-08-11T10:00:00+08:00")),
    )
    report = run_live_evaluation(
        mode="component",
        suite="core",
        repeats=1,
        settings=Settings(
            DEEPSEEK_API_KEY="fixture-key",
            DEEPSEEK_BASE_URL="https://fixture.invalid",
            DEEPSEEK_MODEL="fixture-model",
        ),
    )

    assert report["datasetVersion"] == DATASET_VERSION
    assert report["metrics"]["samples"] == 30
    assert report["metrics"]["uniqueCases"] == 30
    assert 0 <= report["metrics"]["taskSuccessRate"] <= 1
    assert report["metrics"]["stableCaseRate"] == report["metrics"]["taskSuccessRate"]
    assert report["metrics"]["plannedToolSetAccuracy"] == report["metrics"]["toolSelectionAccuracy"]
    assert all("casePass" in result for result in report["results"])
    assert all("plannedToolSetMatch" in result for result in report["results"])
    assert "derived from validated structured state" in " ".join(report["limitations"])


def test_live_cli_module_is_present() -> None:
    assert Path("app/evaluation/live.py").is_file()
