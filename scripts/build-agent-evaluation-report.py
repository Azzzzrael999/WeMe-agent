"""Build a single auditable Agent evaluation V2 report from raw JSON evidence."""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EXPECTED_CATEGORY_COUNTS = {
    "NORMAL_BOOKING": 28,
    "MULTI_PARTY_COORDINATION": 18,
    "COMPLEX_CONSTRAINT": 18,
    "RECOMMENDATION_OR_CONFLICT": 14,
    "POLICY": 14,
    "MODIFY_OR_CANCEL": 18,
    "PREFERENCE_OR_CLARIFICATION": 10,
}
EXPECTED_DIFFICULTY_COUNTS = {"EASY": 72, "MEDIUM": 36, "HARD": 12}
EXPECTED_SPLIT_COUNTS = {"DEV": 80, "VALIDATION": 20, "HOLDOUT": 20}
EXPECTED_FILES = {
    "fixture": "fixture-120.json",
    "liveCore": "live-core-30x3.json",
    "liveFull": "live-full-120x1.json",
    "trajectory": "trajectory-8.json",
    "product": "product-scenarios-16.json",
}
SENSITIVE_MARKERS = (
    '"accessToken"',
    '"confirmationToken"',
    '"serviceToken"',
    '"apiKey"',
    "Bearer ",
)


class ReportError(RuntimeError):
    """Stable validation failure for incomplete or misleading evidence."""


def _read_report(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReportError(f"missing evaluation evidence: {path}") from exc
    if any(marker in raw for marker in SENSITIVE_MARKERS):
        raise ReportError(f"sensitive marker found in evaluation evidence: {path.name}")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReportError(f"invalid JSON evaluation evidence: {path}") from exc
    if not isinstance(payload, dict):
        raise ReportError(f"evaluation evidence must be an object: {path}")
    return payload


def _metrics(report: dict[str, Any], label: str) -> dict[str, Any]:
    metrics = report.get("metrics")
    if not isinstance(metrics, dict):
        raise ReportError(f"{label} report has no metrics object")
    return metrics


def _number(metrics: dict[str, Any], key: str, label: str) -> float:
    value = metrics.get(key)
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise ReportError(f"{label} metric {key} is missing or non-numeric")
    return float(value)


def _count_map(metrics: dict[str, Any], key: str, label: str) -> dict[str, int]:
    value = metrics.get(key)
    if not isinstance(value, dict):
        raise ReportError(f"{label} metric {key} is missing or non-object")
    return {str(item_key): int(item_value) for item_key, item_value in value.items()}


def _require(condition: bool, message: str, gates: list[dict[str, Any]]) -> None:
    gates.append({"name": message, "passed": condition})


def _status_is_pass(report: dict[str, Any]) -> bool:
    return report.get("status") == "PASS"


def _planned_tool_accuracy(metrics: dict[str, Any]) -> float:
    for key in ("plannedToolSetAccuracy", "toolSelectionAccuracy"):
        value = metrics.get(key)
        if isinstance(value, int | float) and not isinstance(value, bool):
            return float(value)
    raise ReportError("live report lacks plannedToolSetAccuracy")


def _git_metadata(workspace: Path) -> tuple[str | None, bool | None]:
    try:
        commit_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
        )
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None, None
    value = commit_result.stdout.strip()
    return value or None, bool(status_result.stdout.strip())


def _evidence_path(path: Path, workspace: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(workspace.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def build_report(input_dir: Path, workspace: Path) -> dict[str, Any]:
    reports = {
        name: _read_report(input_dir / filename) for name, filename in EXPECTED_FILES.items()
    }
    fixture = reports["fixture"]
    live_core = reports["liveCore"]
    live_full = reports["liveFull"]
    trajectory = reports["trajectory"]
    product = reports["product"]
    fixture_metrics = _metrics(fixture, "fixture")
    core_metrics = _metrics(live_core, "live core")
    full_metrics = _metrics(live_full, "live full")
    trajectory_metrics = _metrics(trajectory, "trajectory")
    product_metrics = _metrics(product, "product")
    gates: list[dict[str, Any]] = []

    _require(fixture.get("mode") == "component-fixture", "fixture mode is component-fixture", gates)
    _require(fixture.get("datasetVersion") == "agent-eval-v2-120", "dataset version is agent-eval-v2-120", gates)
    _require(fixture.get("networkCalls") == 0, "fixture network calls are zero", gates)
    _require(_number(fixture_metrics, "totalCases", "fixture") == 120, "fixture has 120 unique cases", gates)
    _require(
        _count_map(fixture_metrics, "categoryCounts", "fixture") == EXPECTED_CATEGORY_COUNTS,
        "fixture category distribution matches the frozen contract",
        gates,
    )
    _require(
        _count_map(fixture_metrics, "difficultyCounts", "fixture")
        == EXPECTED_DIFFICULTY_COUNTS,
        "fixture difficulty distribution matches the frozen contract",
        gates,
    )
    _require(
        _count_map(fixture_metrics, "splitCounts", "fixture") == EXPECTED_SPLIT_COUNTS,
        "fixture split distribution matches the frozen contract",
        gates,
    )
    _require(
        _number(fixture_metrics, "hardConstraintViolations", "fixture") == 0,
        "fixture hard-constraint violations are zero",
        gates,
    )
    _require(
        _number(fixture_metrics, "citationValidity", "fixture") == 1,
        "fixture citation validity is 100%",
        gates,
    )
    _require(
        _number(fixture_metrics, "componentTaskSuccess", "fixture") >= 0.80,
        "fixture component task success is at least 80%",
        gates,
    )

    _require(_status_is_pass(live_core), "live core report status is PASS", gates)
    _require(live_core.get("suite") == "core", "live core suite is core", gates)
    _require(live_core.get("repeats") == 3, "live core repeats each case three times", gates)
    _require(_number(core_metrics, "uniqueCases", "live core") == 30, "live core has 30 unique cases", gates)
    _require(_number(core_metrics, "samples", "live core") == 90, "live core has 90 execution samples", gates)
    _require(
        _number(core_metrics, "stableCaseRate", "live core") >= 0.85,
        "live core stable-case rate is at least 85%",
        gates,
    )
    _require(
        _number(core_metrics, "taskSuccessRate", "live core") >= 0.90,
        "live core task success is at least 90%",
        gates,
    )

    _require(_status_is_pass(live_full), "live full report status is PASS", gates)
    _require(live_full.get("suite") == "full", "live full suite is full", gates)
    _require(live_full.get("repeats") == 1, "live full runs each case once", gates)
    _require(_number(full_metrics, "uniqueCases", "live full") == 120, "live full has 120 unique cases", gates)
    _require(_number(full_metrics, "samples", "live full") == 120, "live full has 120 execution samples", gates)
    _require(
        _number(full_metrics, "sourceFidelityViolations", "live full") == 0,
        "live full source-fidelity violations are zero",
        gates,
    )
    _require(
        _number(full_metrics, "nativeToolProtocol", "live full") == 1,
        "live full native Tool protocol is 100%",
        gates,
    )
    _require(
        _number(full_metrics, "citationValidity", "live full") == 1,
        "live full citation validity is 100%",
        gates,
    )
    _require(
        _number(full_metrics, "taskSuccessRate", "live full") >= 0.90,
        "live full task success is at least 90%",
        gates,
    )
    _require(
        _number(full_metrics, "routeAccuracy", "live full") >= 0.95,
        "live full route accuracy is at least 95%",
        gates,
    )
    _require(
        full_metrics.get("policyRouteAllCorrect") is True,
        "all live full policy cases route correctly",
        gates,
    )
    _require(
        _number(full_metrics, "intentAccuracy", "live full") >= 0.90,
        "live full intent accuracy is at least 90%",
        gates,
    )
    _require(
        _number(full_metrics, "constraintFieldF1", "live full") >= 0.85,
        "live full constraint field F1 is at least 85%",
        gates,
    )
    _require(
        _planned_tool_accuracy(full_metrics) >= 0.90,
        "live full planned Tool set accuracy is at least 90%",
        gates,
    )

    _require(_status_is_pass(trajectory), "isolated product trajectory status is PASS", gates)
    _require(_number(trajectory_metrics, "total", "trajectory") == 8, "isolated trajectory has eight cases", gates)
    _require(_number(trajectory_metrics, "passed", "trajectory") == 8, "all isolated trajectories pass", gates)
    _require(
        trajectory_metrics.get("safetyGatePass") is True,
        "isolated trajectory safety gates all pass",
        gates,
    )
    _require(_status_is_pass(product), "public API scenario status is PASS", gates)
    _require(_number(product_metrics, "total", "product") == 16, "public API suite has 16 scenarios", gates)
    _require(_number(product_metrics, "passed", "product") == 16, "all public API scenarios pass", gates)

    failures: list[dict[str, Any]] = []
    for label, report in reports.items():
        results = report.get("results")
        if not isinstance(results, list):
            continue
        for item in results:
            if not isinstance(item, dict):
                continue
            case_pass = item.get("casePass")
            status = item.get("status")
            if case_pass is False or status == "FAIL" or item.get("errorType"):
                failures.append(
                    {
                        "suite": label,
                        "caseId": item.get("caseId"),
                        "repeat": item.get("repeat"),
                        "errorType": item.get("errorType"),
                        "failure": item.get("failure"),
                    }
                )

    git_commit, working_tree_dirty = _git_metadata(workspace)
    summary = {
        "schemaVersion": "agent-evaluation-summary-v2",
        "generatedAt": datetime.now(UTC).isoformat(),
        "gitCommit": git_commit,
        "workingTreeDirty": working_tree_dirty,
        "datasetVersion": fixture.get("datasetVersion"),
        "status": "PASS" if all(item["passed"] for item in gates) else "FAIL",
        "scope": {
            "componentUniqueCases": 120,
            "liveCoreUniqueCases": 30,
            "liveCoreSamples": 90,
            "liveFullSamples": 120,
            "productTrajectoryCases": 24,
            "isolatedMutationTrajectories": 8,
            "publicApiDialogueScenarios": 16,
        },
        "model": {
            "provider": live_full.get("provider"),
            "configuredModel": live_full.get("configuredModel"),
            "responseModels": live_full.get("responseModels", []),
            "promptVersion": live_full.get("promptVersion"),
            "agentSchemaVersion": live_full.get("agentSchemaVersion"),
        },
        "quality": {
            "fixtureComponentTaskSuccess": fixture_metrics.get("componentTaskSuccess"),
            "liveFullTaskSuccessRate": full_metrics.get("taskSuccessRate"),
            "liveCoreTaskSuccessRate": core_metrics.get("taskSuccessRate"),
            "liveCoreStableCaseRate": core_metrics.get("stableCaseRate"),
            "routeAccuracy": full_metrics.get("routeAccuracy"),
            "intentAccuracy": full_metrics.get("intentAccuracy"),
            "constraintFieldF1": full_metrics.get("constraintFieldF1"),
            "plannedToolSetAccuracy": _planned_tool_accuracy(full_metrics),
            "sourceFidelityViolations": full_metrics.get("sourceFidelityViolations"),
            "citationValidity": full_metrics.get("citationValidity"),
            "nativeToolProtocol": full_metrics.get("nativeToolProtocol"),
            "trajectorySuccess": trajectory_metrics.get("trajectorySuccess"),
            "publicApiScenarioSuccess": product_metrics.get("successRate"),
        },
        "operations": {
            "liveFullLatencyP50Ms": full_metrics.get("latencyP50Ms"),
            "liveFullLatencyP95Ms": full_metrics.get("latencyP95Ms"),
            "trajectoryLatencyP50Ms": trajectory_metrics.get("p50LatencyMs"),
            "trajectoryLatencyP95Ms": trajectory_metrics.get("p95LatencyMs"),
            "publicApiLatencyP50Ms": product_metrics.get("latencyP50Ms"),
            "publicApiLatencyP95Ms": product_metrics.get("latencyP95Ms"),
            "tokenUsage": live_full.get("tokenUsage", {}),
        },
        "gates": gates,
        "failures": failures,
        "evidence": {
            name: _evidence_path(input_dir / filename, workspace)
            for name, filename in EXPECTED_FILES.items()
        },
        "limitations": [
            "The 120 component cases and 24 product trajectories are separate suites and are not summed as one homogeneous question count.",
            "Planned Tool set accuracy is derived from structured component state; observed Tool order, arguments, HITL and side effects are scored by product trajectories.",
            "Results apply to the recorded model, prompt/schema versions and the fixed demonstration environment, not to unrestricted general-purpose Agent capability.",
        ],
    }
    return summary


def _percent(value: Any) -> str:
    if not isinstance(value, int | float) or isinstance(value, bool):
        return "n/a"
    return f"{float(value) * 100:.2f}%"


def report_as_markdown(report: dict[str, Any]) -> str:
    quality = report["quality"]
    operations = report["operations"]
    scope = report["scope"]
    failed_gates = [item["name"] for item in report["gates"] if not item["passed"]]
    failure_lines = [
        f"- `{item.get('suite')}/{item.get('caseId')}`: "
        f"{item.get('errorType') or item.get('failure') or 'case failed'}"
        for item in report["failures"]
    ]
    if not failure_lines:
        failure_lines = ["- 无失败样本。"]
    gate_lines = ["- 无失败门禁。"] if not failed_gates else [f"- {item}" for item in failed_gates]
    return "\n".join(
        [
            "# Agent 评测 V2 报告",
            "",
            f"- 总结：**{report['status']}**",
            f"- 数据集：`{report['datasetVersion']}`",
            f"- Git commit：`{report.get('gitCommit') or 'unknown'}`",
            f"- 工作区含未提交变更：`{report.get('workingTreeDirty')}`",
            f"- 模型：`{report['model'].get('provider')}/{report['model'].get('configuredModel')}`",
            f"- Prompt / Schema：`{report['model'].get('promptVersion')}` / `{report['model'].get('agentSchemaVersion')}`",
            "",
            "## 评测规模",
            "",
            "| 评测层 | 唯一题/场景 | 执行样本 |",
            "|---|---:|---:|",
            f"| 组件完整集 | {scope['componentUniqueCases']} | {scope['liveFullSamples']} |",
            f"| 真实模型核心稳定集 | {scope['liveCoreUniqueCases']} | {scope['liveCoreSamples']} |",
            f"| 隔离写入/HITL 轨迹 | {scope['isolatedMutationTrajectories']} | {scope['isolatedMutationTrajectories']} |",
            f"| 公开 API 多轮场景 | {scope['publicApiDialogueScenarios']} | {scope['publicApiDialogueScenarios']} |",
            "",
            "## 质量指标",
            "",
            "| 指标 | 结果 |",
            "|---|---:|",
            f"| Fixture Component Task Success | {_percent(quality.get('fixtureComponentTaskSuccess'))} |",
            f"| Live Full Task Success | {_percent(quality.get('liveFullTaskSuccessRate'))} |",
            f"| Core Task Success | {_percent(quality.get('liveCoreTaskSuccessRate'))} |",
            f"| Core Stable Case Rate | {_percent(quality.get('liveCoreStableCaseRate'))} |",
            f"| Route Accuracy | {_percent(quality.get('routeAccuracy'))} |",
            f"| Intent Accuracy | {_percent(quality.get('intentAccuracy'))} |",
            f"| Constraint Field F1 | {_percent(quality.get('constraintFieldF1'))} |",
            f"| Planned Tool Set Accuracy | {_percent(quality.get('plannedToolSetAccuracy'))} |",
            f"| Citation Validity | {_percent(quality.get('citationValidity'))} |",
            f"| Native Tool Protocol | {_percent(quality.get('nativeToolProtocol'))} |",
            f"| 隔离轨迹成功率 | {_percent(quality.get('trajectorySuccess'))} |",
            f"| 公开 API 多轮成功率 | {_percent(quality.get('publicApiScenarioSuccess'))} |",
            "",
            "## 性能",
            "",
            "| 指标 | P50 | P95 |",
            "|---|---:|---:|",
            f"| Live Full Component | {operations.get('liveFullLatencyP50Ms')} ms | {operations.get('liveFullLatencyP95Ms')} ms |",
            f"| 隔离产品轨迹 | {operations.get('trajectoryLatencyP50Ms')} ms | {operations.get('trajectoryLatencyP95Ms')} ms |",
            f"| 公开 API 多轮 | {operations.get('publicApiLatencyP50Ms')} ms | {operations.get('publicApiLatencyP95Ms')} ms |",
            "",
            "## 失败门禁",
            "",
            *gate_lines,
            "",
            "## 失败样本",
            "",
            *failure_lines,
            "",
            "## 口径限制",
            "",
            *[f"- {item}" for item in report["limitations"]],
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Agent evaluation V2 summary.")
    parser.add_argument("--input-dir", type=Path, default=Path("artifacts/agent-eval-v2"))
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-markdown", type=Path)
    args = parser.parse_args()
    workspace = Path(__file__).resolve().parent.parent
    input_dir = args.input_dir.resolve()
    output_json = args.output_json or input_dir / "summary.json"
    output_markdown = args.output_markdown or input_dir / "report.md"
    try:
        report = build_report(input_dir, workspace)
    except ReportError as exc:
        print(f"agent evaluation report failed: {exc}")
        return 1
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_markdown.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    output_markdown.write_text(report_as_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report["status"],
                "outputJson": str(output_json),
                "outputMarkdown": str(output_markdown),
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
