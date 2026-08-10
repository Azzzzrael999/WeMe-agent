# 07. 测试与评测规范

## 0. Spec 1.1 Agent Loop 评测门槛

新增评测必须区分三层，不得把 Fixture 组件评测写成真实 E2E：

1. `component-fixture`：确定性验证 Schema、Evaluator、Tool Gate、求解器和停止条件，网络调用为0。
2. `trajectory-integration`：运行完整 LangGraph 与 Java Tool mock/测试服务，验证 Tool 调用序列、重复调用拦截、HITL 前零副作用、checkpoint 恢复和冲突重规划。
3. `live-model`：显式启用 DeepSeek 后运行隔离数据集；报告 provider/model、Prompt/Schema 版本、每例重复次数、成功率、延迟、Token/成本和失败样本。未配置 Key 时跳过且不得标记 PASS。

现有 `python -m app.evaluation` 报告的 `mode` 固定为 `component-fixture`，指标名为 `componentTaskSuccess`；它不能命名为端到端成功。LangGraph 全图、HITL、checkpoint 与冲突重规划证据由 `trajectory-integration` 测试和 Compose Smoke 单独给出。

必须覆盖：

- 原生 `tools/tool_calls` 请求和 `assistant -> tool -> assistant` 消息闭环。
- 非法 JSON、额外参数、伪造 userId/runId、越权 Tool、重复指纹、模型提前结束和超预算。
- Requirement 首次失败、结构化 feedback、一次修复成功/失败和需用户澄清。
- Java CONFLICT 后重新读取事实、排除失败候选、保留硬约束、候选发生变化、最多2次重规划。
- `Hard Constraint Violation = 0`、`HITL Before Side Effects = 100%`、`Loop Normal Termination >= 99%`（fixture/integration）和重复 Tool 执行率为0。

## 1. 测试目标

测试必须证明三件事：

1. Java在并发、重复请求和异步消息下保持业务正确。
2. Agent能够稳定路由、提取约束、选择工具和恢复工作流。
3. Docker部署可以复现完整Golden Path。

## 2. Java测试矩阵

### 2.1 单元测试

| 模块 | 重点 |
|---|---|
| TimeSlotCalculator | 起止边界、跨日拒绝、90分钟槽位 |
| BookingValidator | 容量、设备、时间、权限 |
| MeetingStateMachine | 修改、取消和非法状态转换 |
| IdempotencyService | 首次、重复、不同payload复用key |
| OutboxPublisher | 状态转换和重试时间 |
| ToolAuthorization | 风险等级和用户权限 |
| EmployeeAdministration | 字段规范化、自我停用/降权和乐观版本 |
| NotificationService | 用户隔离、读状态幂等和接收人并集 |

### 2.2 集成测试

使用Testcontainers或测试Compose启动真实依赖：

- MySQL唯一约束。
- Redis Lua预占和token释放。
- 普通预约事务回滚。
- 热门受理事务同时写request和Outbox。
- MQ消费后创建会议。
- Outbox重复发布时消费者幂等。
- Agent Tool Gateway鉴权和参数校验。
- ADMIN 员工新增、编辑、启停、密码重置和 EMPLOYEE 越权拒绝。
- 会议创建、修改、取消与 HOT 成功的通知事务、隔离和重复消费幂等。

### 2.3 并发正确性测试

#### CT-01 同房间同槽位

- 100至1000个并发请求。
- 不同用户抢同一房间和相同连续槽位。
- 断言最多一个 `CONFIRMED`。
- 断言slot表不存在重复唯一键。

#### CT-02 幂等确认

- 相同用户、相同幂等键并发调用确认接口。
- 所有成功响应指向同一meetingId或requestNo。
- 数据库只存在一条业务记录。

#### CT-03 多槽位交叉

- 请求A占15:00至16:30。
- 请求B占15:30至16:00。
- 请求C占16:30至17:00。
- A和B不能同时成功，C可以成功。

#### CT-04 必须参加者冲突

- 不同房间但相同REQUIRED员工、相同时段。
- 最多一个会议成功。

#### CT-05 热门异步竞争

- 多个请求均得到PENDING受理或按限流返回。
- 最终只有一个SUCCESS，其余为CONFLICT。
- 每个requestNo必须进入终态。

### 2.4 性能报告

报告必须记录：

- CPU、内存和操作系统。
- Docker资源限制。
- 数据量和并发模型。
- 请求总数、成功数、冲突数。
- P50、P95、P99。
- Redis、MySQL和MQ配置。
- 正确性断言结果。

不以夸大QPS为目标；热点竞争下“零重复成功”比绝对吞吐更重要。

## 3. Agent单元与图测试

### 3.1 Router

覆盖每种Intent：

- CREATE_MEETING
- FIND_COMMON_TIME
- RECOMMEND_ROOM
- MODIFY_MEETING
- CANCEL_MEETING
- QUERY_POLICY
- UPDATE_PREFERENCE

### 3.2 Requirement

验证：

- 相对日期转绝对日期。
- 30/60/90分钟持续时间。
- REQUIRED与OPTIONAL划分。
- 硬/软约束。
- 同名员工触发澄清。
- 上下文会议解析。

### 3.3 Graph

验证条件边：

- 规则查询不调用Scheduling。
- 普通预约依次经过Requirement、可选Policy、Scheduling和HITL。
- EDIT返回重新校验。
- REJECT结束且不调用写工具。
- PENDING进入等待状态。
- SUCCESS恢复后结束。
- CONFLICT恢复后重新规划。
- 重复业务回调只处理一次。

### 3.4 Tool测试

- 参数Schema校验。
- Tool白名单。
- Java 401/403/409/503映射。
- 超时有限重试。
- 大结果摘要。
- 副作用工具重复调用幂等。

## 4. OR-Tools测试

采用确定性fixture，不调用LLM：

| 场景 | 断言 |
|---|---|
| 单一可行方案 | 返回指定房间和时间 |
| 多房间 | 返回成本最低房间 |
| REQUIRED冲突 | 不产生非法方案 |
| OPTIONAL冲突 | 允许但成本增加 |
| 容量不足 | 候选被过滤 |
| 设备不足 | 候选被过滤 |
| 90分钟会议 | 必须连续3个槽位 |
| 偏好15点后 | 其他条件相同优先15点后 |
| Top 3 | 方案不重复且按成本升序 |
| 无解 | 返回可解释冲突类别 |

硬约束验证器独立于求解器实现，用于复核每个返回方案。

## 5. RAG测试

- 文档checksum相同不重复入库。
- Markdown Front Matter 缺字段、非法枚举、非 ACTIVE 状态或错误时区必须拒绝。
- 文本型 PDF 可提取正文并使用同名 sidecar 元数据；扫描型或空文本 PDF 必须明确失败，不做 OCR。
- Markdown 按标题路径切片，超长章节按段落拆分；chunkId 在相同内容重复导入时保持稳定。
- 相同 documentId 内容变化时旧 Qdrant points 被替换；删除源文件不得自动清空已有索引。
- 导入状态按 `INDEXING -> INDEXED` 或 `INDEXING -> FAILED` 转换，只有 Qdrant 完整 upsert 成功才记录 `indexed_at`。
- 架构评审问题召回架构评审文档。
- VIP问题召回VIP规则。
- 回答引用的chunkId真实存在。
- `open_policy_chunks`不能打开本轮候选之外的chunk。
- 无答案时返回未找到证据。

## 6. Agent离线评测集

### 6.1 数据格式

```json
{
  "caseId": "create-001",
  "input": "明天下午三点预约一小时会议室，六个人，要白板",
  "context": {
    "now": "2026-08-10T10:00:00+08:00",
    "userId": 1001
  },
  "expectedIntent": "CREATE_MEETING",
  "expectedConstraints": {
    "durationMinutes": 60,
    "requiredFeatures": ["WHITEBOARD"]
  },
  "expectedTools": [
    "search_available_rooms",
    "create_booking_draft"
  ],
  "forbiddenTools": ["confirm_booking"]
}
```

### 6.2 首版数量

总计30至50条：

| 类别 | 数量建议 |
|---|---:|
| 普通预约 | 8 |
| 多人协调 | 6 |
| 复杂约束 | 6 |
| 推荐与冲突 | 5 |
| 规则问答 | 5 |
| 修改取消 | 6 |
| 偏好与澄清 | 4 |

### 6.3 指标

| 指标 | 计算方式 | 目标 |
|---|---|---:|
| Intent Accuracy | 正确Intent/总数 | >= 90% |
| Constraint Field F1 | 字段级精确率和召回率 | >= 85% |
| Tool Selection Accuracy | 必需Tool命中且无禁用Tool | >= 90% |
| Hard Constraint Violation | 非法方案/全部方案 | 0% |
| Citation Validity | 有效引用/全部引用 | 100% |
| Component Task Success | 完成组件预期终态/总数 | 报告真实值，不称 E2E |

真实模型另要求 Route Accuracy >= 95%（Policy 必须全对）、Source Fidelity Violation=0、Native Tool Protocol=100%、Citation Validity=100%，以及隔离公共 API 自然语言轨迹成功率 >= 80%。

这些目标针对固定模型、Prompt版本和演示数据，不宣称通用能力。

## 7. 端到端Golden Path

### E2E-01 普通预约

1. 登录。
2. 输入普通预约请求。
3. 收到3个以内候选。
4. 接受候选。
5. 会议进入CONFIRMED。
6. 会议列表可见。
7. Agent Trace完整。

### E2E-02 热门冲突与恢复

1. 将会议室标记HOT。
2. 创建冲突预约作为竞争条件。
3. Agent确认热门请求并获得PENDING。
4. MQ处理后返回CONFLICT。
5. Agent从checkpoint恢复。
6. Scheduling Agent给出新房间或时间。
7. 用户再次确认并成功。

### E2E-03 HITL编辑

1. Agent生成15:00候选。
2. 用户编辑为15:30。
3. 系统重新查询和求解。
4. Java不直接执行未复核的编辑参数。

### E2E-04 规则问答

1. 提问VIP会议室规则。
2. 只执行Policy路径。
3. 返回有效引用。

### E2E-05 手动预约

1. Agent服务停止或不配置DeepSeek Key。
2. 用户仍可通过表单创建会议。
3. Java并发规则仍生效。

## 8. Docker Smoke Test

从空数据卷启动：

1. `docker compose config --quiet`通过。
2. 所有容器健康。
3. Flyway和Alembic成功。
4. RAG seed成功。
5. 登录成功。
6. 手动预约成功。
7. Agent Golden Path成功。
8. 容器重启后会议和checkpoint仍存在。

## 9. 安全测试

- 普通员工不能修改他人会议。
- Python Tool调用缺少Service Token被拒绝。
- AgentContextToken audience不正确被拒绝。
- 过期确认令牌不能执行。
- 模型伪造的userId被忽略，以令牌subject为准。
- Tool参数超出时间范围或返回上限被拒绝。
- Trace接口只能查看当前用户Run，ADMIN除外。
- 当前 ADMIN 不能停用或降权自己，员工密码/哈希不会出现在 API 与日志中。
- ADMIN 也不能读取或标记其他用户的通知。

## 10. 不做的测试

首版不建设：

- Chaos Monkey或网络故障注入。
- 多机房灾备测试。
- Redis Cluster和MySQL主从切换。
- 长时间稳定性Soak Test。
- 真实外部日历和视频供应商契约测试。

这些限制必须在README中说明。
