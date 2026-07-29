from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import pytest

from app.evaluation.__main__ import main
from app.evaluation.corpus import EXPECTED_CATEGORY_COUNTS, load_day7_cases
from app.evaluation.runner import report_as_json, run_day7_evaluation
from app.schemas.agent import Intent


def test_day7_corpus_has_exact_documented_shape() -> None:
    cases = load_day7_cases()

    assert len(cases) == 40
    assert len({case.case_id for case in cases}) == 40
    assert Counter(case.category for case in cases) == EXPECTED_CATEGORY_COUNTS


def test_day7_offline_evaluation_validates_fixture_safety_invariants() -> None:
    report = run_day7_evaluation()
    metrics = report.metrics

    assert report.mode == "offline-deterministic-fixture"
    assert report.network_calls == 0
    assert metrics.total_cases == 40
    assert metrics.category_counts == EXPECTED_CATEGORY_COUNTS
    assert metrics.intent_accuracy >= 0.90
    assert metrics.constraint_f1 >= 0.85
    assert metrics.tool_selection_accuracy >= 0.85
    assert metrics.hard_constraint_candidates_checked > 0
    assert metrics.hard_constraint_violations == 0
    assert metrics.hard_constraint_violation_rate == 0.0
    assert metrics.citations_checked == 5
    assert metrics.citations_valid == 5
    assert metrics.citation_validity == 1.0
    assert metrics.end_to_end_task_success >= 0.80
    assert {result.prediction.intent for result in report.results} == set(Intent)


def test_day7_report_is_deterministic_machine_readable_json() -> None:
    first = report_as_json(run_day7_evaluation())
    second = report_as_json(run_day7_evaluation())

    assert first == second
    payload = json.loads(first)
    assert payload["schemaVersion"] == "day7-agent-evaluation-v1"
    assert payload["metrics"]["totalCases"] == 40


def test_day7_evaluation_cli_can_write_an_explicit_runtime_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output_path = tmp_path / "day7-agent-evaluation.json"
    monkeypatch.setattr(sys, "argv", ["agent-evaluation", "--output", str(output_path)])

    assert main() == 0
    assert json.loads(output_path.read_text(encoding="utf-8"))["networkCalls"] == 0
