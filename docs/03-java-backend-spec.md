# 03. Java 后端规范

## 1. Java 服务职责

Java服务是业务系统的唯一事实和安全边界，负责：

- JWT与RBAC。
- 员工、部门、会议室和设备。
- 会议CRUD和状态机。
- 参与者忙闲和会议室可用性。
- 预约草案、确认令牌和最终确认。
- 普通同步预约和热门异步预约。
- Redis预占、限流和幂等。
- MySQL事务、唯一约束和Outbox。
- RocketMQ生产、消费、重试与结果回调。
- 站内通知。
- Agent Tool白名单、参数校验和审计。
- Python SSE代理。

## 2. 模块结构

建议按业务模块组织，避免全部堆入 `controller/service/mapper`：

```text
com.example.meeting
├─ auth/
│  ├─ api/
│  ├─ application/
│  ├─ domain/
│  └─ infrastructure/
├─ organization/
├─ room/
├─ meeting/
├─ booking/
│  ├─ api/
│  ├─ application/
│  ├─ domain/
│  └─ infrastructure/
├─ notification/
├─ agentgateway/
│  ├─ api/                    # 前端Agent入口
│  ├─ internal/               # Python Tool入口
│  ├─ client/                 # Python Agent客户端
│  └─ audit/
├─ outbox/
├─ mq/
├─ common/
│  ├─ error/
│  ├─ idempotency/
│  ├─ security/
│  ├─ trace/
│  └─ web/
└─ MeetingApplication.java
```

## 3. 核心领域规则

### 3.1 时间规则

- 时区固定为 `Asia/Shanghai`。
- 预约起止时间必须落在整点或半点。
- 结束时间必须晚于开始时间。
- 默认最短 30 分钟，最长 4 小时。
- 一次会议占用 `[start, end)` 对应的连续槽位。

### 3.2 房间规则

- 房间必须为 `ACTIVE`。
- 房间容量必须大于等于参会人数。
- 房间必须包含全部 `requiredFeatures`。
- 只有管理员可以配置房间为 `HOT`。

### 3.3 参与者规则

- 发起人自动加入 REQUIRED。
- REQUIRED 参与者忙碌槽位必须唯一。
- OPTIONAL 参与者允许已有冲突，但调度时计入惩罚。
- 手动预约和Agent预约使用相同业务服务。

### 3.4 修改与取消

- 只有发起人或ADMIN可修改、取消。
- 修改采用“新槽位写入 + 旧槽位释放”的同一事务。
- 修改失败时原会议保持不变。
- 取消使用条件更新防止重复状态转换。

## 4. 普通同步预约算法

### 4.1 输入

```java
record ConfirmBookingCommand(
    String confirmationToken,
    String idempotencyKey,
    Long organizerId,
    Long roomId,
    LocalDateTime startAt,
    LocalDateTime endAt,
    List<Long> requiredParticipantIds,
    List<Long> optionalParticipantIds,
    String runId,
    String toolCallId
) {}
```

### 4.2 执行步骤

1. 验证确认令牌存在、归属正确、未使用、未过期。
2. 查询幂等记录；已成功则返回历史结果。
3. 执行权限、时间、容量、设备和状态校验。
4. 计算全部30分钟槽位编号。
5. Redis Lua原子预占会议室和REQUIRED参与者槽位。
6. 开启MySQL事务。
7. 创建或锁定幂等记录。
8. 插入会议主记录和参与者。
9. 批量插入会议室槽位。
10. 批量插入REQUIRED参与者忙碌槽位。
11. 写入Outbox领域事件。
12. 标记确认令牌已使用和幂等记录成功。
13. 提交事务。
14. 释放当前请求拥有的Redis预占；刷新可用性缓存。

数据库唯一键冲突统一转换为 `BOOKING_CONFLICT`，不得向调用方泄露SQL异常。

### 4.3 Redis预占键

同一日期使用Redis Cluster hash tag，保证Lua涉及的键位于同一slot：

```text
meeting:hold:{2026-08-12}:room:101:slot:30
meeting:hold:{2026-08-12}:employee:2001:slot:30
```

值只保存随机预占令牌，例如 `hold_uuid`。用户、过期时间和业务上下文记录在应用日志或请求上下文中，不依赖Redis值承载。

默认TTL为30秒。

Lua语义：

```text
for each key:
  if key exists and value != currentToken:
    return conflict
for each key:
  set key currentValue PX ttl NX
return success
```

释放时必须先比较token，只删除当前请求拥有的键。

### 4.4 数据库最终约束

```sql
UNIQUE KEY uq_room_slot(room_id, booking_date, slot_index)
UNIQUE KEY uq_required_employee_slot(employee_id, booking_date, slot_index)
```

取消会议时删除正式槽位记录，会议主记录保留 `CANCELLED` 状态。

### 4.5 死锁控制

- 槽位按 `booking_date, slot_index` 升序插入。
- REQUIRED参与者ID升序处理。
- 对可识别的数据库死锁最多重试2次，并使用短随机退避。
- 唯一键冲突不重试。

## 5. 幂等设计

### 5.1 幂等记录

唯一键：

```text
(user_id, operation, idempotency_key)
```

状态：

- `PROCESSING`
- `SUCCEEDED`
- `FAILED_RETRYABLE`
- `FAILED_FINAL`

记录请求摘要哈希和响应摘要。同一幂等键对应不同请求摘要时返回 `IDEMPOTENCY_KEY_REUSED`。

### 5.2 Tool幂等

副作用Tool唯一键：

```text
(run_id, tool_call_id, tool_name)
```

Agent恢复后重复发送相同调用时，Java直接返回之前结果。

## 6. 热门异步预约

### 6.1 受理事务

确认热门草案时：

1. 校验确认令牌和幂等键。
2. Redis执行用户限流和重复请求控制。
3. 在一个MySQL事务中插入：
   - `booking_request(PENDING)`；
   - `message_outbox(BOOKING_COMMAND)`；
   - 幂等记录；
   - 确认令牌使用状态。
4. 返回 `PENDING + requestNo`。

受理阶段不创建正式会议和槽位。

### 6.2 MQ消费事务

消费 `BOOKING_COMMAND` 时：

1. 用 `eventId` 检查消费幂等表。
2. 条件更新 `booking_request: PENDING -> PROCESSING`。
3. 执行与同步预约相同的数据库校验和槽位插入。
4. 成功时写入会议、槽位、参与者、通知Outbox和 `BOOKING_RESULT(SUCCESS)`。
5. 唯一键冲突时将请求更新为 `CONFLICT`，写入 `BOOKING_RESULT(CONFLICT)`。
6. 同一事务写入消费记录。

Agent回调不放在数据库事务内；由结果事件的独立消费者完成。

## 7. Transactional Outbox

### 7.1 Outbox状态

- `NEW`
- `SENDING`
- `SENT`
- `RETRY`
- `DEAD`

### 7.2 发布器

- 每500ms扫描一批NEW/RETRY记录。
- 使用数据库抢占或条件更新避免多实例重复并发发送。
- MQ发送成功后更新SENT。
- 失败记录重试次数和下次重试时间。
- 即使Outbox重复发送，消费者也必须幂等。

### 7.3 事件信封

```json
{
  "eventId": "evt_uuid",
  "eventType": "BOOKING_COMMAND",
  "aggregateType": "BOOKING_REQUEST",
  "aggregateId": "req_20260812_001",
  "traceId": "trc_uuid",
  "runId": "run_uuid",
  "occurredAt": "2026-08-12T10:00:00+08:00",
  "schemaVersion": 1,
  "payload": {}
}
```

## 8. RocketMQ设计

| Topic | Tag | 用途 |
|---|---|---|
| meeting-booking | BOOKING_COMMAND | 热门预约最终执行 |
| meeting-booking | BOOKING_RESULT | 唤醒Agent或通知前端 |
| meeting-domain | MEETING_CONFIRMED | 创建站内通知、视频动作 |
| meeting-domain | MEETING_CHANGED | 更新通知和外部动作 |
| meeting-domain | MEETING_CANCELLED | 通知和外部补偿 |

消费语义：

- 至少一次。
- 业务终态和 `event_consume_record` 双重幂等。
- 不在业务代码中宣称Exactly Once。

## 9. Agent Tool Gateway

### 9.1 工具白名单

| Tool | 风险 | Java能力 |
|---|---|---|
| resolve_employees | READ | 按姓名/部门解析员工 |
| get_employee_free_busy | READ | 返回指定窗口忙碌槽位 |
| search_available_rooms | READ | 按时间、容量、设备查询 |
| get_recent_meeting | READ | 查询当前用户最近会议/草案 |
| create_booking_draft | DRAFT | 创建无副作用预约草案 |
| create_reschedule_draft | DRAFT | 创建改期差异草案 |
| create_cancellation_preview | DRAFT | 创建取消预览 |
| confirm_booking | WRITE | 仅HITL恢复后允许调用 |
| confirm_reschedule | WRITE | 仅HITL恢复后允许调用 |
| confirm_cancellation | WRITE | 仅HITL恢复后允许调用 |

`search_meeting_policy` 和 `solve_schedule` 位于Python内部，不经过Java。

### 9.2 每次Tool调用必须校验

- 服务令牌。
- AgentContextToken签名和audience。
- runId、traceId一致。
- Tool是否在白名单。
- 当前用户权限。
- 参数Bean Validation。
- 时间窗口和返回数量上限。
- Tool风险级别。
- Tool幂等记录。

Day 3 冻结的内部鉴权语义：

- `X-Service-Token` 必须与环境变量中的服务令牌恒定时间比较；缺失或错误返回 `SERVICE_TOKEN_INVALID`。
- `Authorization` 必须是 Java 签发的短期 HS256 `AgentContextToken`，`aud=agent-service`，并包含 `sub/roles/traceId/runId/exp`；签名、audience、过期时间或字段无效返回 `AGENT_CONTEXT_INVALID`。
- `X-Trace-Id`、`X-Run-Id` 必须与 Token claim 一致，`X-Tool-Call-Id` 必须非空；请求体中的用户标识一律忽略，以 Token `sub` 为准。
- `(runId, toolCallId, toolName)` 是 Tool 调用幂等键；相同摘要返回已记录响应，不同摘要返回 `IDEMPOTENCY_KEY_REUSED`。

## 10. SSE Gateway

- 前端不直连Agent服务。
- Java使用非阻塞HTTP客户端或适合长连接的客户端连接Python。
- Java不修改业务事件语义，只补充鉴权和trace字段。
- 设置首包、空闲和总超时。
- 客户端断开时关闭上游流；已提交业务请求不回滚。
- `GET /api/v1/agent/runs/{runId}`用于断线恢复。

## 11. 错误码

| Code | HTTP | 含义 |
|---|---:|---|
| AUTH_REQUIRED | 401 | 未登录或令牌失效 |
| SERVICE_TOKEN_INVALID | 401 | 内部服务令牌缺失或错误 |
| AGENT_CONTEXT_INVALID | 401 | AgentContextToken 或上下文头无效 |
| FORBIDDEN | 403 | 无业务权限 |
| VALIDATION_ERROR | 400 | 参数错误 |
| DRAFT_EXPIRED | 409 | 草案或确认令牌过期 |
| DRAFT_ALREADY_USED | 409 | 草案已确认或拒绝 |
| BOOKING_CONFLICT | 409 | 会议室或必须参加者冲突 |
| IDEMPOTENCY_KEY_REUSED | 409 | 幂等键对应不同请求 |
| MEETING_NOT_FOUND | 404 | 会议不存在或当前用户不可查看 |
| BOOKING_REQUEST_NOT_FOUND | 404 | 热门预约请求不存在或当前用户不可查看 |
| MEETING_STATE_CONFLICT | 409 | 会议状态或版本不允许当前修改/取消操作 |
| BOOKING_PENDING | 202 | 热门请求已受理 |
| TOOL_NOT_ALLOWED | 403 | Agent工具不在白名单 |
| AGENT_UNAVAILABLE | 503 | Python服务不可用 |
| DEPENDENCY_UNAVAILABLE | 503 | Redis、MQ等依赖不可用 |

统一错误响应：

```json
{
  "code": "BOOKING_CONFLICT",
  "message": "会议室或必须参加者在该时段已被占用",
  "traceId": "trc_uuid",
  "details": []
}
```

## 12. Java测试要求

- Service单元测试覆盖状态转换和权限。
- Testcontainers启动MySQL、Redis和RocketMQ进行集成测试。
- 并发测试验证唯一槽位。
- 重复幂等键测试。
- Outbox重复发布与消费者重复消费测试。
- 热门请求从PENDING进入SUCCESS/CONFLICT测试。
- Tool Gateway越权、参数越界和重复调用测试。
- SSE事件映射测试。
