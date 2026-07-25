# 企业会议智能调度系统

一个面向简历展示的 Java + Python Multi-Agent 完整项目。系统使用 Java 承担会议业务、高并发预约和可靠异步执行，使用 Python、LangGraph、DeepSeek 与 OR-Tools 承担自然语言理解、会议规划、规则检索和冲突优化。

## 项目定位

- 技术展示优先，业务功能保持最小闭环。
- Java 侧重点：并发一致性、Redis、MySQL 事务、RocketMQ、Transactional Outbox、幂等和服务边界。
- Python 侧重点：Supervisor + 3 个专业 Agent、Tool Calling、HITL、持久化恢复、约束求解、简化 RAG 和 Agent 评测。
- 前端侧重点：聊天、候选方案确认、会议基础管理和 Agent Trace。
- 部署方式：Docker Compose 一键启动。

## 规范文档

1. [Spec 总入口与冻结决策](SPEC.md)
2. [项目总览与范围](docs/00-project-overview.md)
3. [功能与验收规范](docs/01-functional-spec.md)
4. [系统架构规范](docs/02-system-architecture.md)
5. [Java 后端规范](docs/03-java-backend-spec.md)
6. [Multi-Agent 规范](docs/04-agent-spec.md)
7. [数据模型与 API 契约](docs/05-data-and-api-spec.md)
8. [Docker 部署规范](docs/06-docker-deployment.md)
9. [测试与评测规范](docs/07-test-and-evaluation.md)
10. [开发计划](docs/08-development-plan.md)

## Day 1 / Day 2 / Day 3 / Day 4 本地启动

当前真实进度与验证证据以 [开发交接](docs/HANDOFF.md) 为准。首次启动先生成仅保存在本机、且已被 Git 忽略的随机环境变量文件：

```powershell
.\scripts\New-LocalEnv.ps1
docker compose config --quiet
docker compose -f compose.yaml -f compose.dev.yaml up -d --build
docker compose ps
```

基础 `compose.yaml` 只发布前端端口；`compose.dev.yaml` 额外发布 Java、Python、Mock 和基础设施端口，供本地联调。演示账号为 `zhangsan / demo-password`。

模块质量门槛：

```powershell
Push-Location business-service
.\mvnw.cmd verify
Pop-Location

Push-Location agent-service
uv sync --frozen --group dev
uv run ruff check .
uv run mypy app
uv run pytest
Pop-Location

Push-Location frontend
npm ci
npm run type-check
npm run build
Pop-Location
```

Day 2 增加 Java 手动会议创建、列表、详情、修改、取消、Redis Lua 预占、MySQL 最终唯一约束和创建幂等。完整 Compose 启动后可执行：

```powershell
# 顺序验证：90分钟、幂等、修改失败回滚、查询和取消
.\scripts\smoke-day2.ps1

# 100请求抢同一房间/槽位：恰好一个成功
python .\scripts\concurrency-day2.py --mode room

# 100个相同幂等请求：全部返回同一个meetingId
python .\scripts\concurrency-day2.py --mode idempotency
```

Day 3 增加 Transactional Outbox、RocketMQ 热门预约最终执行、业务结果事件、草案/确认令牌、内部 Tool Gateway 和 Java SSE 代理边界。完整 Compose 启动后执行：

```powershell
# Tool 鉴权、查询工具、HOT 草案、PENDING、MQ SUCCESS/CONFLICT、Tool 重放
python .\scripts\smoke-day3.py

# 使用上一步输出的成功 requestNo 重放同一 BOOKING_COMMAND，验证消费者幂等
.\scripts\replay-day3-booking-command.ps1 -RequestNo BR202608120001
```

Compose 会通过一次性 `rocketmq-topic-init` 创建 `meeting-booking`、`meeting-domain` 和固定 Consumer Group。`APP_HOT_BOOKING_ENABLED=true` 时，选择演示 HOT 房间 103 的草案确认走异步链路。

Day 4 增加了 Supervisor、Requirement、Policy、Scheduling 四个固定 Agent、Pydantic 结构化状态、OpenAI-compatible DeepSeek Provider、Java 只读 Tool Client、Qdrant 政策检索、Agent 元数据和 Java 代理 SSE。默认 `AGENT_MODEL_PROVIDER=fixture` 使用确定性本地模型，适合不配置 DeepSeek Key 的开发/Smoke；此时健康接口仍如实显示 `DEGRADED`。要使用 DeepSeek，请仅在本地 `.env` 设置 `AGENT_MODEL_PROVIDER=deepseek` 和真实 Key，切勿提交该文件。

完整 Compose 以 fixture 运行后，可验证一条经 Nginx → Java → Python → Java Tool 的无副作用 Day 4 请求，并核对 SSE 与持久化 Trace：

```powershell
python .\scripts\smoke-day4.py
```

Day 4 不包含 OR-Tools 候选求解、草案/确认 HITL、Redis checkpoint、热门预约结果恢复或视频会议写工具；这些能力严格留给 Day 5。

## 首版成功标准

以下演示链路必须完整运行：

> “下周三下午帮张三、李四安排一个 90 分钟架构评审，要大屏，尽量在研发楼，并创建视频会议。”

系统应完成需求提取、制度检索、多人忙闲查询、OR-Tools 求解、候选方案解释、用户确认、Java 并发预约、热门预约异步处理、Agent 恢复和 Trace 展示。
