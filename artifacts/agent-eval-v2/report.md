# Agent 评测 V2 报告

- 总结：**FAIL**
- 数据集：`agent-eval-v2-120`
- Git commit：`d9c164e3aec7fdf7d234f2d4d60b174e207ebca8`
- 工作区含未提交变更：`True`
- 模型：`deepseek/deepseek-v4-flash`
- Prompt / Schema：`meeting-agent-prompts-v11` / `meeting-agent-state-v7`

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
| Live Full Task Success | 93.33% |
| Core Task Success | 96.67% |
| Core Stable Case Rate | 96.67% |
| Route Accuracy | 97.50% |
| Intent Accuracy | 95.00% |
| Constraint Field F1 | 100.00% |
| Planned Tool Set Accuracy | 97.50% |
| Citation Validity | 100.00% |
| Native Tool Protocol | 100.00% |
| 隔离轨迹成功率 | 100.00% |
| 公开 API 多轮成功率 | 81.25% |

## 性能

| 指标 | P50 | P95 |
|---|---:|---:|
| Live Full Component | 3651.89 ms | 4985.14 ms |
| 隔离产品轨迹 | 6191.57 ms | 8559.68 ms |
| 公开 API 多轮 | 8510.37 ms | 14572.23 ms |

## 失败门禁

- live full report status is PASS
- live full source-fidelity violations are zero
- all live full policy cases route correctly
- public API scenario status is PASS
- all public API scenarios pass

## 失败样本

- `liveCore/recommend-006`: case failed
- `liveCore/recommend-006`: case failed
- `liveCore/recommend-006`: case failed
- `liveFull/normal-009`: case failed
- `liveFull/recommend-006`: case failed
- `liveFull/recommend-012`: case failed
- `liveFull/recommend-013`: case failed
- `liveFull/policy-005`: case failed
- `liveFull/policy-007`: case failed
- `liveFull/policy-009`: case failed
- `liveFull/policy-013`: case failed
- `product/create-mixed-language`: turn 1 expected HITL, got WAITING_INPUT
- `product/modify-explicit-id-reject`: turn 1 expected HITL, got WAITING_INPUT
- `product/modify-ambiguous-target-clarified`: turn 1 expected WAITING_INPUT, got HITL

## 口径限制

- The 120 component cases and 24 product trajectories are separate suites and are not summed as one homogeneous question count.
- Planned Tool set accuracy is derived from structured component state; observed Tool order, arguments, HITL and side effects are scored by product trajectories.
- Results apply to the recorded model, prompt/schema versions and the fixed demonstration environment, not to unrestricted general-purpose Agent capability.
