from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import pytest

from app.evaluation.__main__ import main
from app.evaluation.corpus import (
    DATASET_VERSION,
    EXPECTED_CATEGORY_COUNTS,
    EXPECTED_DIFFICULTY_COUNTS,
    EXPECTED_SPLIT_COUNTS,
    load_day7_cases,
    validate_evaluation_corpus,
)
from app.evaluation.runner import report_as_json, run_day7_evaluation
from app.schemas.agent import Intent


def test_day7_corpus_has_exact_documented_shape() -> None:
    cases = load_day7_cases()

    assert len(cases) == 120
    assert len({case.case_id for case in cases}) == 120
    assert len({case.input for case in cases}) == 120
    assert Counter(case.category for case in cases) == EXPECTED_CATEGORY_COUNTS
    assert Counter(case.difficulty for case in cases) == EXPECTED_DIFFICULTY_COUNTS
    assert Counter(case.split for case in cases) == EXPECTED_SPLIT_COUNTS
    assert {case.expected_intent for case in cases} == set(Intent)
    validate_evaluation_corpus(cases)


def test_v2_corpus_forbids_post_hitl_writes_and_has_consistent_tools() -> None:
    cases = load_day7_cases()
    hitl_writes = {"confirm_booking", "confirm_reschedule", "confirm_cancellation"}

    for case in cases:
        expected = set(case.expected_tools)
        forbidden = set(case.forbidden_tools)
        assert case.tags
        assert not expected.intersection(forbidden)
        assert hitl_writes.issubset(forbidden)
        assert not expected.intersection(hitl_writes)
        if case.expected_intent is Intent.QUERY_POLICY:
            assert case.expected_citation_ids
        else:
            assert not case.expected_citation_ids


def test_v2_corpus_validator_rejects_a_write_tool_before_hitl() -> None:
    cases = list(load_day7_cases())
    cases[0] = cases[0].model_copy(
        update={"expected_tools": [*cases[0].expected_tools, "confirm_booking"]}
    )

    with pytest.raises(ValueError, match="forbidden tool"):
        validate_evaluation_corpus(tuple(cases))


def test_day7_offline_evaluation_validates_fixture_safety_invariants() -> None:
    report = run_day7_evaluation()
    metrics = report.metrics

    assert report.mode == "component-fixture"
    assert report.network_calls == 0
    assert report.dataset_version == DATASET_VERSION
    assert metrics.total_cases == 120
    assert metrics.category_counts == EXPECTED_CATEGORY_COUNTS
    assert metrics.difficulty_counts == EXPECTED_DIFFICULTY_COUNTS
    assert metrics.split_counts == EXPECTED_SPLIT_COUNTS
    assert metrics.intent_accuracy >= 0.90
    assert metrics.constraint_f1 >= 0.85
    assert metrics.tool_selection_accuracy >= 0.85
    assert metrics.hard_constraint_candidates_checked > 0
    assert metrics.hard_constraint_violations == 0
    assert metrics.hard_constraint_violation_rate == 0.0
    assert metrics.citations_checked == 14
    assert metrics.citations_valid == 14
    assert metrics.citation_validity == 1.0
    assert metrics.component_task_success >= 0.95
    assert set(metrics.component_success_by_category) == set(EXPECTED_CATEGORY_COUNTS)
    assert set(metrics.component_success_by_difficulty) == set(EXPECTED_DIFFICULTY_COUNTS)
    assert set(metrics.component_success_by_split) == set(EXPECTED_SPLIT_COUNTS)
    assert {result.prediction.intent for result in report.results} == set(Intent)


def test_day7_report_is_deterministic_machine_readable_json() -> None:
    first = report_as_json(run_day7_evaluation())
    second = report_as_json(run_day7_evaluation())

    assert first == second
    payload = json.loads(first)
    assert payload["schemaVersion"] == "component-fixture-evaluation-v3"
    assert payload["datasetVersion"] == DATASET_VERSION
    assert payload["metrics"]["totalCases"] == 120


def test_day7_evaluation_cli_can_write_an_explicit_runtime_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output_path = tmp_path / "day7-agent-evaluation.json"
    monkeypatch.setattr(sys, "argv", ["agent-evaluation", "--output", str(output_path)])

    assert main() == 0
    assert json.loads(output_path.read_text(encoding="utf-8"))["networkCalls"] == 0
