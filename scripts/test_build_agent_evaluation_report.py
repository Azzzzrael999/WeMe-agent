from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import ModuleType


def load_report_module() -> ModuleType:
    script = Path(__file__).with_name("build-agent-evaluation-report.py")
    spec = importlib.util.spec_from_file_location("build_agent_evaluation_report", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REPORT = load_report_module()


def fixture_report() -> dict[str, object]:
    return {
        "schemaVersion": "component-fixture-evaluation-v3",
        "datasetVersion": "agent-eval-v2-120",
        "mode": "component-fixture",
        "networkCalls": 0,
        "metrics": {
            "totalCases": 120,
            "categoryCounts": REPORT.EXPECTED_CATEGORY_COUNTS,
            "difficultyCounts": REPORT.EXPECTED_DIFFICULTY_COUNTS,
            "splitCounts": REPORT.EXPECTED_SPLIT_COUNTS,
            "hardConstraintViolations": 0,
            "citationValidity": 1.0,
            "componentTaskSuccess": 0.95,
        },
        "results": [],
    }


def live_report(*, suite: str, repeats: int, unique: int, samples: int) -> dict[str, object]:
    return {
        "schemaVersion": "live-model-component-v3",
        "mode": "live-model-component",
        "suite": suite,
        "repeats": repeats,
        "status": "PASS",
        "provider": "deepseek",
        "configuredModel": "fixture-live-model",
        "promptVersion": "prompt-v1",
        "agentSchemaVersion": "schema-v1",
        "responseModels": ["fixture-live-model"],
        "tokenUsage": {"inputTokens": 10, "outputTokens": 5},
        "metrics": {
            "uniqueCases": unique,
            "samples": samples,
            "taskSuccessRate": 0.95,
            "stableCaseRate": 0.90,
            "routeAccuracy": 1.0,
            "policyRouteAllCorrect": True,
            "intentAccuracy": 0.98,
            "constraintFieldF1": 0.93,
            "plannedToolSetAccuracy": 0.95,
            "sourceFidelityViolations": 0,
            "nativeToolProtocol": 1.0,
            "citationValidity": 1.0,
            "latencyP50Ms": 1000,
            "latencyP95Ms": 2000,
        },
        "results": [],
    }


def trajectory_report() -> dict[str, object]:
    return {
        "mode": "live-model-trajectory",
        "status": "PASS",
        "metrics": {
            "total": 8,
            "passed": 8,
            "trajectorySuccess": 1.0,
            "safetyGatePass": True,
            "p50LatencyMs": 1000,
            "p95LatencyMs": 2000,
        },
        "results": [],
    }


def product_report() -> dict[str, object]:
    return {
        "mode": "public-api-adversarial-dialogue",
        "status": "PASS",
        "metrics": {
            "total": 16,
            "passed": 16,
            "successRate": 1.0,
            "latencyP50Ms": 3000,
            "latencyP95Ms": 5000,
        },
        "results": [],
    }


class AgentEvaluationSummaryTest(unittest.TestCase):
    def _write_evidence(self, root: Path) -> None:
        payloads = {
            "fixture-120.json": fixture_report(),
            "live-core-30x3.json": live_report(
                suite="core", repeats=3, unique=30, samples=90
            ),
            "live-full-120x1.json": live_report(
                suite="full", repeats=1, unique=120, samples=120
            ),
            "trajectory-8.json": trajectory_report(),
            "product-scenarios-16.json": product_report(),
        }
        for name, payload in payloads.items():
            (root / name).write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )

    def test_builds_pass_summary_without_combining_unique_case_counts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_evidence(root)

            report = REPORT.build_report(root, Path(__file__).resolve().parent.parent)

            self.assertEqual(report["status"], "PASS")
            self.assertEqual(report["scope"]["componentUniqueCases"], 120)
            self.assertEqual(report["scope"]["productTrajectoryCases"], 24)
            self.assertEqual(report["scope"]["liveCoreSamples"], 90)
            self.assertNotIn("totalUniqueCases", report["scope"])
            self.assertIn("Planned Tool Set Accuracy", REPORT.report_as_markdown(report))

    def test_failed_live_report_cannot_be_summarized_as_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_evidence(root)
            path = root / "live-full-120x1.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["status"] = "FAIL"
            path.write_text(json.dumps(payload), encoding="utf-8")

            report = REPORT.build_report(root, Path(__file__).resolve().parent.parent)

            self.assertEqual(report["status"], "FAIL")
            self.assertTrue(
                any(
                    item["name"] == "live full report status is PASS"
                    and not item["passed"]
                    for item in report["gates"]
                )
            )

    def test_sensitive_marker_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_evidence(root)
            path = root / "trajectory-8.json"
            path.write_text('{"accessToken":"not-a-real-token"}', encoding="utf-8")

            with self.assertRaises(REPORT.ReportError):
                REPORT.build_report(root, Path(__file__).resolve().parent.parent)


if __name__ == "__main__":
    unittest.main()
