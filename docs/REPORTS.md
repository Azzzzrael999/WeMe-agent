# Day 7 验收报告

验收日期：2026-08-11（Asia/Shanghai）。本报告记录的是可复现的 Day 7 证据，不宣称真实 DeepSeek 模型质量，也不自动开启 Day 8。

## 结论

**Day 7：PASS。** 新环境空卷启动、连续三次 Golden Path、Java 并发正确性、Agent 离线评测、完整 Compose 和公共 API Smoke 均已实际执行通过。所有 Smoke 与评测使用确定性 fixture，不调用真实 DeepSeek。

## 可复现命令与结果

| 命令 | 实际结果 |
|---|---|
| `docker run --rm -v "${PWD}\\business-service:/workspace" -w /workspace maven:3.9.11-eclipse-temurin-21 ./mvnw -B -ntp verify` | PASS：53 tests，0 failures/errors/skips；Jar 与 Spotless 通过。 |
| `Push-Location agent-service; uv sync --frozen --group dev; uv run ruff check .; uv run mypy app; uv run pytest; Pop-Location` | PASS：79 packages audited；Ruff 通过；mypy 覆盖 37 个 source files；pytest **57 passed**。仅有上游 LangGraph pending-deprecation warning。 |
| `Push-Location agent-service; uv run python -m app.evaluation; Pop-Location` | PASS：40 条离线 fixture 评测，`networkCalls=0`。 |
| `Push-Location frontend; npm ci; npm run type-check; npm run build; Pop-Location` | PASS：49 modules production build。 |
| `docker compose -f compose.yaml -f compose.dev.yaml config --quiet` | PASS。 |
| `python scripts/smoke-day5.py --public-trace --restart-agent-service` | PASS：普通计划、EDIT、ACCEPT、checkpoint 重启恢复、HOT PENDING、MQ CONFLICT 回调与重新规划。 |
| `python scripts/smoke-day6.py` | PASS：手动会议创建/修改/取消、Java 代理 SSE/HITL/Trace、房间 availability 与管理员 RBAC。 |
| `powershell -ExecutionPolicy Bypass -File scripts\\Test-Day7EmptyVolume.ps1` | PASS：独立 Compose project `meeting-scheduler-day7-d0a2945b` 使用全新命名卷，三次完整 Golden Path（其中一次 Agent 重启），结束后仅停止临时容器与网络，未使用 `-v`。 |

## Java 并发正确性

`MeetingConcurrencyIntegrationTest` 覆盖并验证数据库最终状态：

- CT-01：同房间同槽位竞争，且补强最终重复槽位检查；
- CT-02：相同幂等键并发确认；
- CT-03：交叉时段 A/B 竞争与相邻不重叠 C 成功；
- CT-04：不同房间但共享 REQUIRED 参会者的竞争；
- CT-05：5 条 HOT 预受理命令并发终结，断言 1 SUCCESS、4 CONFLICT、所有请求进入终态、无重复房间槽位与 5 条消费记录。

在当前完整主栈上还执行了真实 HTTP 压测（100 请求、32 workers、同一虚构用户，脚本经公共取消 API 回收成功会议）：

| 场景 | 成功 | 冲突 | 唯一成功会议 | Mean | P50 | P95 | P99 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 同房间、同 90 分钟槽位 | 1 | 99 | 1 | 512.41 ms | 500.78 ms | 803.27 ms | 1288.93 ms |
| 相同幂等键 | 100 | 0 | 1 | 521.43 ms | 507.67 ms | 745.70 ms | 761.68 ms |

这些数字是本机 Docker 开发环境的回归基线，不是容量承诺或生产 SLO。

## Agent 离线评测

评测入口是 `python -m app.evaluation`，provider 为 `FixtureModelProvider + InMemoryPolicyRetriever + ScheduleSolver`。数据集共 40 条：普通预约 8、多方协调 6、复杂约束 6、推荐/冲突 5、政策 5、修改/取消 6、偏好/澄清 4。

| 指标 | 结果 |
|---|---:|
| Intent accuracy | 1.0 |
| Constraint precision / recall / F1 | 1.0 / 1.0 / 1.0 |
| Tool selection accuracy | 1.0 |
| Component task success | 1.0 |
| OR-Tools 独立硬约束候选检查 | 60 |
| 硬约束违例 | 0（违例率 0.0） |
| 政策引用有效性 | 5 / 5 |
| 网络调用 | 0 |

这是固定 fixture、内存政策语料和确定性求解器的组件基线，不是 LangGraph 或真实模型端到端评测。真实 DeepSeek 的质量、线上延迟与外部知识覆盖不在此报告的证明范围；全图轨迹和写入业务事实的端到端正确性由 Python trajectory integration 与 Java/Compose Smoke 单独验证。

## Day 7 稳定性修复

真实 HOT Smoke 曾暴露 LangGraph 同步生成器被 Starlette/AnyIO 跨 worker 线程续跑的竞态：确认节点已经持久化 `WAITING_BUSINESS_RESULT`，流结束时却可能出现 `AGENT_GRAPH_FAILED`。修复后，图迭代始终在单个 `agent-sse-producer` 线程中运行，标准 SSE 帧通过队列逐帧交给响应；Redis checkpoint Saver 对 load/mutate/save 加同一把锁，图流使用同步 durability。新增线程亲和和 checkpoint 并发写回归测试；修复后的真实 Day 5 Smoke 与空卷三连测均通过。

## 环境与资源上限

- 宿主：Windows 11（10.0.22621）、13th Gen Intel Core i9-13900HX、32 logical processors、15.70 GiB RAM。
- Docker：Docker Desktop 4.83.0，Engine 29.6.2，Linux/amd64，WSL2 kernel 6.18.33.2。
- Compose 声明的常驻服务上限约为 **4.2 GiB RAM / 5.75 CPU**；短暂 RocketMQ 初始化容器另有最多 **384 MiB / 0.75 CPU**。这只是部署护栏，不代表测得的实际资源使用。
- 完整开发 Compose 最终状态：MySQL、Redis、RocketMQ NameServer/Broker、Qdrant、business-service、agent-service、frontend、video-provider-mock 均为 `healthy`；`rocketmq-store-init` 与 `rocketmq-topic-init` 是预期的 `Exited (0)` 初始化任务。

完整的已解析镜像内容标识见 [image-manifest-day7.json](image-manifest-day7.json)。

## 安全与限制

- 静态扫描（明确排除本地 `.env`）结果：`secretPrefixMatches=0`、`.env.example` 危险默认敏感值 `0`。
- `.env.example` 默认启用 `AGENT_CALLBACK_ENABLED=true`；本机已有的旧本地环境值在最终主栈 Smoke 前以**进程环境覆盖**启用，未读取、打印或改写 `.env`。
- 空卷验收的本地环境由 `scripts/New-LocalEnv.ps1` 随机生成；临时 project 结束后保留命名卷，方便人工检查，不执行 `down -v`。
- 运行时仍固定为 Supervisor、Requirement、Policy、Scheduling 四个产品 Agent。没有新增 Day 8 或范围外能力。

## 2026-08-13 真实模型修复验收补充

本节覆盖旧报告中“尚未执行真实模型质量评测”的结论。详细命令和失败分类见 `docs/HANDOFF.md` 第 22 节，脱敏 JSON 位于 `artifacts/`。

| 分层 | 最终结论 | 核心结果 |
|---|---|---|
| component fixture | PASS | 40 条，网络 0；Intent/Tool/Citation 100%，Constraint F1 96.76%，硬约束违规 0；组件任务成功率 82.5%，不称 E2E。 |
| integration/Compose | PASS | Java 61 tests、Python 76 tests、Frontend type/build、完整 Compose healthy；三类 HITL 与旧 token 作废回归通过。 |
| live-model component core | PASS | 12 条 × 3：Route 100%、Intent 97.22%、Constraint 100%、Tool 94.44%、Native Tool/Citation 100%、Source violation 0。 |
| live-model component full | **FAIL** | 40 条 × 1：Tool 80%、Source violation 1、Citation 80% 未过门禁；整体 component 结论因此仍是 FAIL。 |
| live-model trajectory | PASS | 公共 Java API 8 条隔离轨迹 7 条通过，87.5%；固定 ID 9001 不存在的准确负例保留为失败。 |
