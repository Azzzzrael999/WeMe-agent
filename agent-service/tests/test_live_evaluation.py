from pathlib import Path

from app.config import Settings
from app.evaluation.live import CORE_CASE_IDS, CORE_CASES, run_live_evaluation


def test_live_component_is_explicitly_skipped_without_provider_config() -> None:
    report = run_live_evaluation(
        mode="component",
        suite="core",
        repeats=3,
        settings=Settings(DEEPSEEK_API_KEY="", DEEPSEEK_BASE_URL="", DEEPSEEK_MODEL=""),
    )

    assert len(CORE_CASE_IDS) == 12
    assert len(CORE_CASES) == 12
    assert len(set(CORE_CASE_IDS)) == 12
    assert {case.expected_intent.value for case in CORE_CASES} == {
        "CREATE_MEETING",
        "QUERY_POLICY",
        "MODIFY_MEETING",
        "CANCEL_MEETING",
        "FIND_COMMON_TIME",
        "RECOMMEND_ROOM",
    }
    assert report["mode"] == "live-model-component"
    assert report["status"] == "SKIPPED"
    assert report["results"] == []


def test_live_trajectory_never_claims_component_success() -> None:
    report = run_live_evaluation(mode="trajectory", suite="full", repeats=1)

    assert report["mode"] == "live-model-trajectory"
    assert report["status"] == "SKIPPED"
    assert "Java SSE" in str(report["reason"])


def test_live_cli_module_is_present() -> None:
    assert Path("app/evaluation/live.py").is_file()
