# Agent 评测 V2 报告

- 总结：**PASS**
- 数据集：`agent-eval-v2-120`
- Git commit：`6e28c3515ba76bc8ccb0950030ce8fa2219c606c`
- 工作区含未提交变更：`True`
- 模型：`deepseek/deepseek-v4-flash`
- Prompt / Schema：`meeting-agent-prompts-v12` / `meeting-agent-state-v7`

## 评测规模

| 评测层 | 唯一题/场景 | 执行样本 |
|---|---:|---:|
| 组件完整集 | 120 | 120 |
| 真实模型核心稳定集 | 30 | 90 |
| 隔离写入/HITL 轨迹 | 8 | 8 |
| 公开 API 多轮场景 | 16 | 16 |

## 质量指标

| 指标 | 结果 |
|---|---:|
| Fixture Component Task Success | 100.00% |
| Live Full Task Success | 99.17% |
| Core Task Success | 98.89% |
| Core Stable Case Rate | 96.67% |
| Route Accuracy | 100.00% |
| Intent Accuracy | 100.00% |
| Constraint Field F1 | 100.00% |
| Planned Tool Set Accuracy | 100.00% |
| Citation Validity | 100.00% |
| Native Tool Protocol | 100.00% |
| 隔离轨迹成功率 | 100.00% |
| 公开 API 多轮成功率 | 100.00% |

## 性能

| 指标 | P50 | P95 |
|---|---:|---:|
| Live Full Component | 3087.54 ms | 4316.27 ms |
| 隔离产品轨迹 | 7014.18 ms | 9332.53 ms |
| 公开 API 多轮 | 9032.69 ms | 14606.07 ms |

## 失败门禁

- 无失败门禁。

## 失败样本

- `liveCore/normal-001`: MODEL_UNAVAILABLE
- `liveFull/policy-005`: case failed

## 口径限制

- The 120 component cases and 24 product trajectories are separate suites and are not summed as one homogeneous question count.
- Planned Tool set accuracy is derived from structured component state; observed Tool order, arguments, HITL and side effects are scored by product trajectories.
- Results apply to the recorded model, prompt/schema versions and the fixed demonstration environment, not to unrestricted general-purpose Agent capability.
