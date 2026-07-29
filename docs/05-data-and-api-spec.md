# 05. 数据模型与 API 契约

## 1. 数据库规划

同一个MySQL实例建立两个逻辑库：

```text
meeting_business   # Java拥有
meeting_agent      # Python拥有
```

Java和Python使用不同数据库账号，禁止跨库写入。Redis和Qdrant同样通过key前缀或collection隔离。

## 2. Java核心表

下表列出关键字段，完整DDL通过Flyway生成。

### 2.1 sys_user

```text
id BIGINT PK
username VARCHAR(64) UNIQUE
password_hash VARCHAR(255)
display_name VARCHAR(64)
email VARCHAR(128)
department_id BIGINT
role VARCHAR(32)                 # EMPLOYEE/ADMIN
status VARCHAR(16)               # ACTIVE/DISABLED
created_at DATETIME(3)
updated_at DATETIME(3)
```

索引：`department_id`、`display_name`。

### 2.2 department

```text
id BIGINT PK
name VARCHAR(64) UNIQUE
default_building VARCHAR(64)
default_floor VARCHAR(32)
status VARCHAR(16)
```

### 2.3 meeting_room

```text
id BIGINT PK
code VARCHAR(32) UNIQUE
name VARCHAR(64)
building VARCHAR(64)
floor VARCHAR(32)
capacity INT
room_type VARCHAR(32)
is_hot BOOLEAN
status VARCHAR(16)
version INT
created_at DATETIME(3)
updated_at DATETIME(3)
```

### 2.4 room_feature / meeting_room_feature

```text
room_feature(id, code UNIQUE, name)
meeting_room_feature(room_id, feature_id, PRIMARY KEY(room_id, feature_id))
```

预置Feature：`WHITEBOARD`、`LARGE_SCREEN`、`VIDEO_CONFERENCE`、`PROJECTOR`。

### 2.5 meeting

```text
id BIGINT PK
meeting_no VARCHAR(40) UNIQUE
title VARCHAR(128)
meeting_type VARCHAR(32)
organizer_id BIGINT
room_id BIGINT
start_at DATETIME(3)
end_at DATETIME(3)
status VARCHAR(24)               # CONFIRMED/CANCELLED/COMPLETED
source VARCHAR(16)               # MANUAL/AGENT
run_id VARCHAR(64) NULL
request_no VARCHAR(64) NULL
version INT
created_at DATETIME(3)
updated_at DATETIME(3)
cancelled_at DATETIME(3) NULL
```

索引：`organizer_id,start_at`、`room_id,start_at`、`run_id`、`request_no`。

说明：草案和热门PENDING请求分别存储在 `booking_draft` 与 `booking_request`，不会提前创建正式meeting。

### 2.6 meeting_participant

```text
id BIGINT PK
meeting_id BIGINT
employee_id BIGINT
participant_type VARCHAR(16)     # REQUIRED/OPTIONAL
UNIQUE(meeting_id, employee_id)
```

### 2.7 meeting_room_slot

```text
id BIGINT PK
meeting_id BIGINT
room_id BIGINT
booking_date DATE
slot_index SMALLINT              # 0..47
start_at DATETIME(3)
end_at DATETIME(3)
UNIQUE(room_id, booking_date, slot_index)
```

### 2.8 employee_busy_slot

```text
id BIGINT PK
meeting_id BIGINT
employee_id BIGINT
booking_date DATE
slot_index SMALLINT
start_at DATETIME(3)
end_at DATETIME(3)
UNIQUE(employee_id, booking_date, slot_index)
```

仅为REQUIRED参与者写入。

### 2.9 booking_draft

```text
id BIGINT PK
confirmation_token VARCHAR(80) UNIQUE
user_id BIGINT
run_id VARCHAR(64)
tool_call_id VARCHAR(80)
operation VARCHAR(24)            # CREATE/RESCHEDULE/CANCEL
payload_json JSON
payload_hash VARCHAR(64)
status VARCHAR(24)               # PENDING/USED/REJECTED/EXPIRED
version INT
expires_at DATETIME(3)
created_at DATETIME(3)
used_at DATETIME(3) NULL
```

### 2.10 booking_request

```text
id BIGINT PK
request_no VARCHAR(64) UNIQUE
user_id BIGINT
run_id VARCHAR(64)
trace_id VARCHAR(64)
tool_call_id VARCHAR(80)
operation VARCHAR(24)
payload_json JSON
status VARCHAR(24)               # PENDING/PROCESSING/SUCCESS/CONFLICT/FAILED
meeting_id BIGINT NULL
error_code VARCHAR(64) NULL
error_message VARCHAR(255) NULL
created_at DATETIME(3)
updated_at DATETIME(3)
```

### 2.11 idempotency_record

```text
id BIGINT PK
user_id BIGINT
operation VARCHAR(48)
idempotency_key VARCHAR(80)
request_hash VARCHAR(64)
status VARCHAR(24)
response_json JSON NULL
expires_at DATETIME(3)
UNIQUE(user_id, operation, idempotency_key)
```

### 2.12 message_outbox

```text
id BIGINT PK
event_id VARCHAR(64) UNIQUE
event_type VARCHAR(64)
aggregate_type VARCHAR(64)
aggregate_id VARCHAR(64)
topic VARCHAR(128)
tag VARCHAR(64)
trace_id VARCHAR(64)
run_id VARCHAR(64) NULL
payload_json JSON
status VARCHAR(16)
retry_count INT
next_retry_at DATETIME(3) NULL
created_at DATETIME(3)
sent_at DATETIME(3) NULL
```

### 2.13 event_consume_record

```text
id BIGINT PK
consumer_group VARCHAR(128)
event_id VARCHAR(64)
status VARCHAR(16)
consumed_at DATETIME(3)
UNIQUE(consumer_group, event_id)
```

### 2.14 notification

```text
id BIGINT PK
user_id BIGINT
type VARCHAR(32)
title VARCHAR(128)
content VARCHAR(1000)
related_meeting_id BIGINT NULL
read_at DATETIME(3) NULL
created_at DATETIME(3)
```

### 2.15 agent_tool_audit

```text
id BIGINT PK
trace_id VARCHAR(64)
run_id VARCHAR(64)
tool_call_id VARCHAR(80)
tool_name VARCHAR(64)
user_id BIGINT
risk_level VARCHAR(16)
request_hash VARCHAR(64)
result_code VARCHAR(64)
response_json JSON NULL
duration_ms BIGINT
created_at DATETIME(3)
UNIQUE(run_id, tool_call_id, tool_name)
```

### 2.16 video_conference_link

```text
id BIGINT PK
meeting_id BIGINT UNIQUE
provider VARCHAR(32)
external_meeting_id VARCHAR(80)
join_url VARCHAR(255)
status VARCHAR(24)
idempotency_key VARCHAR(80) UNIQUE
created_at DATETIME(3)
updated_at DATETIME(3)
```

## 3. Python核心表

### 3.1 agent_thread

```text
thread_id VARCHAR(64) PK
user_id BIGINT
title VARCHAR(128)
created_at DATETIME(3)
updated_at DATETIME(3)
```

### 3.2 agent_run

```text
run_id VARCHAR(64) PK
thread_id VARCHAR(64)
trace_id VARCHAR(64)
user_id BIGINT
intent VARCHAR(32)
status VARCHAR(32)
question_summary VARCHAR(500)
answer_summary TEXT NULL
model_call_count INT
tool_call_count INT
input_tokens BIGINT
output_tokens BIGINT
duration_ms BIGINT NULL
error_code VARCHAR(64) NULL
created_at DATETIME(3)
finished_at DATETIME(3) NULL
```

### 3.3 agent_step

```text
step_id VARCHAR(64) PK
run_id VARCHAR(64)
sequence_no INT
agent_name VARCHAR(64)
node_name VARCHAR(64)
status VARCHAR(24)
input_summary TEXT
output_summary TEXT
duration_ms BIGINT
error_code VARCHAR(64) NULL
created_at DATETIME(3)
UNIQUE(run_id, sequence_no)
```

### 3.4 agent_tool_call

```text
tool_call_id VARCHAR(80) PK
run_id VARCHAR(64)
tool_name VARCHAR(64)
risk_level VARCHAR(16)
sanitized_args JSON
result_summary TEXT
status VARCHAR(24)
duration_ms BIGINT
created_at DATETIME(3)
```

### 3.5 user_scheduling_preference

```text
user_id BIGINT PK
preferences_json JSON
updated_from_run_id VARCHAR(64)
updated_at DATETIME(3)
```

### 3.6 agent_business_event

```text
event_id VARCHAR(64) PK
run_id VARCHAR(64)
request_no VARCHAR(64)
event_type VARCHAR(64)
payload_json JSON
processed_at DATETIME(3)
```

### 3.7 rag_document

```text
document_id VARCHAR(64) PK
title VARCHAR(255)
document_type VARCHAR(64)
source_path VARCHAR(500)
version VARCHAR(32)
checksum VARCHAR(64)
status VARCHAR(24)
chunk_count INT
created_at DATETIME(3)
indexed_at DATETIME(3) NULL
```

## 4. API通用规范

### 4.1 前缀

- 公共Java API：`/api/v1`
- Java内部Tool API：`/internal/v1/tools`
- Python内部API：`/internal/v1`

### 4.2 通用成功响应

```json
{
  "data": {},
  "traceId": "trc_uuid",
  "timestamp": "2026-08-12T10:00:00+08:00"
}
```

SSE和文件响应不使用该信封。

### 4.3 通用错误响应

```json
{
  "code": "VALIDATION_ERROR",
  "message": "startAt必须落在30分钟槽位",
  "details": [
    {"field": "startAt", "reason": "INVALID_SLOT_BOUNDARY"}
  ],
  "traceId": "trc_uuid"
}
```

## 5. 公共Java API

### 5.1 鉴权

```text
POST /api/v1/auth/login
GET  /api/v1/auth/me
```

登录请求：

```json
{"username": "zhangsan", "password": "demo-password"}
```

### 5.2 会议室

```text
GET    /api/v1/rooms
GET    /api/v1/rooms/{roomId}
GET    /api/v1/rooms/{roomId}/availability?from=&to=
POST   /api/v1/admin/rooms
PUT    /api/v1/admin/rooms/{roomId}
PATCH  /api/v1/admin/rooms/{roomId}/status
```

`GET /api/v1/rooms` 为 EMPLOYEE 只返回 `ACTIVE` 会议室；ADMIN 返回全部状态，以便启用已停用的房间。`GET /api/v1/rooms/{roomId}` 成功时返回与列表 item 相同的 `RoomItemView`，新增的 `version` 用于管理员乐观更新：

```json
{
  "id": 101,
  "code": "RD-301",
  "name": "研发楼301",
  "building": "研发楼",
  "floor": "3F",
  "capacity": 8,
  "roomType": "STANDARD",
  "isHot": false,
  "status": "ACTIVE",
  "version": 0,
  "features": [{"code": "WHITEBOARD", "name": "白板"}]
}
```

会议室可用性只暴露房间自身的 30 分钟 `[start,end)` 槽位，不暴露其他会议或人员信息。`from` 与 `to` 必须是 `Asia/Shanghai` 的带偏移时间、落在 30 分钟边界、`from < to` 且窗口不超过 14 天：

```json
{
  "roomId": 101,
  "from": "2026-08-19T13:00:00+08:00",
  "to": "2026-08-19T15:00:00+08:00",
  "availableSlots": [
    {"startAt": "2026-08-19T13:00:00+08:00", "endAt": "2026-08-19T13:30:00+08:00", "available": true}
  ]
}
```

以下是管理员 `PUT` 修改请求；`POST` 创建使用同一形状但省略 `expectedVersion`，`featureCodes` 为已有会议室设备编码：

```json
{
  "code": "RD-303",
  "name": "研发楼303",
  "building": "研发楼",
  "floor": "3F",
  "capacity": 8,
  "roomType": "STANDARD",
  "isHot": false,
  "featureCodes": ["WHITEBOARD"],
  "expectedVersion": 0
}
```

`POST` 不带 `expectedVersion`；`PATCH /api/v1/admin/rooms/{roomId}/status` 请求为 `{"status":"ACTIVE|INACTIVE","expectedVersion":0}`。所有 `/api/v1/admin/rooms/**` 仅允许 ADMIN。不存在或对 EMPLOYEE 不可见的房间返回 `ROOM_NOT_FOUND`（404）；重复房间编码返回 `ROOM_CODE_CONFLICT`（409）；版本或状态竞争返回 `ROOM_STATE_CONFLICT`（409）。

### 5.3 会议

```text
GET    /api/v1/meetings?from=&to=&status=&page=&size=
GET    /api/v1/meetings/{meetingId}
POST   /api/v1/meetings                    # 手动同步预约
PUT    /api/v1/meetings/{meetingId}
DELETE /api/v1/meetings/{meetingId}
GET    /api/v1/booking-requests/{requestNo}
```

手动创建请求：

```json
{
  "title": "架构评审",
  "meetingType": "ARCHITECTURE_REVIEW",
  "roomId": 101,
  "startAt": "2026-08-19T15:00:00+08:00",
  "endAt": "2026-08-19T16:30:00+08:00",
  "requiredParticipantIds": [1001, 1002],
  "optionalParticipantIds": [1003],
  "createVideoConference": false
}
```

请求头必须包含：

```text
Idempotency-Key: uuid
```

Day 2 手动会议接口使用以下冻结响应数据；所有数据仍包在 4.2 节的通用成功信封中：

```json
{
  "id": 9001,
  "meetingNo": "MTG202608190001",
  "title": "架构评审",
  "meetingType": "ARCHITECTURE_REVIEW",
  "organizerId": 1001,
  "organizerName": "张三",
  "roomId": 101,
  "roomCode": "RD-301",
  "roomName": "研发楼 301",
  "startAt": "2026-08-19T15:00:00+08:00",
  "endAt": "2026-08-19T16:30:00+08:00",
  "status": "CONFIRMED",
  "source": "MANUAL",
  "participants": [
    {
      "employeeId": 1001,
      "displayName": "张三",
      "participantType": "REQUIRED"
    }
  ],
  "version": 0,
  "createdAt": "2026-08-11T10:00:00+08:00",
  "updatedAt": "2026-08-11T10:00:00+08:00",
  "cancelledAt": null
}
```

会议列表响应数据为：

```json
{
  "items": [],
  "total": 0
}
```

- 普通员工的列表和详情只包含自己发起或参与的会议；ADMIN 可以查看全部会议。
- 会议列表 `page` 默认 1，`size` 默认 20、最大 100；`total` 表示过滤条件下的完整记录数。
- 同时提供 `from` 与 `to` 时，查询窗口最大 14 天。
- 只有发起人或 ADMIN 可以修改、取消会议。
- 发起人无论是否出现在请求数组中，都由服务端加入 REQUIRED；同一员工不能同时作为 REQUIRED 和 OPTIONAL。
- `requiredParticipantIds` 与 `optionalParticipantIds` 合计最多 100 人；房间容量按去重后并包含发起人的总人数校验。
- `createVideoConference` 在 Day 2 只接受 `false`；视频会议外部副作用保留到后续 Mock 业务链路。
- 手动创建成功状态固定为 `CONFIRMED`，来源固定为 `MANUAL`。
- `POST /api/v1/meetings` 强制使用 `Idempotency-Key`；同一用户、同一键、同一请求返回同一 `meetingId`，同一键对应不同请求返回 `IDEMPOTENCY_KEY_REUSED`。

修改请求复用创建请求的业务字段，并额外要求当前版本：

```json
{
  "title": "架构评审（调整）",
  "meetingType": "ARCHITECTURE_REVIEW",
  "roomId": 102,
  "startAt": "2026-08-19T15:30:00+08:00",
  "endAt": "2026-08-19T17:00:00+08:00",
  "requiredParticipantIds": [1001, 1002],
  "optionalParticipantIds": [1003],
  "createVideoConference": false,
  "expectedVersion": 0
}
```

- 修改只允许 `CONFIRMED` 会议；成功后 `version` 加一。
- 修改在同一事务内替换会议字段、参与者和槽位；任何校验、唯一键或版本冲突都必须回滚，原会议保持不变。
- `DELETE /api/v1/meetings/{meetingId}` 使用条件状态转换；成功后状态为 `CANCELLED` 并释放正式槽位，重复取消返回稳定冲突错误。

热门预约状态查询返回：

```json
{
  "requestNo": "BR202608120001",
  "status": "PENDING",
  "meetingId": null,
  "errorCode": null,
  "errorMessage": null,
  "createdAt": "2026-08-12T10:00:00+08:00",
  "updatedAt": "2026-08-12T10:00:00+08:00"
}
```

- 只有请求所属用户或 ADMIN 可以查看；不可见时统一返回 `BOOKING_REQUEST_NOT_FOUND`。
- Day 3 中 HOT 由所选会议室 `is_hot=true` 决定；`APP_HOT_BOOKING_ENABLED=true` 时 HOT 草案确认先返回 HTTP 202 和 `PENDING + requestNo`，不会在受理事务中创建正式会议；开关为 false 时返回 `DEPENDENCY_UNAVAILABLE`，不得静默改为同步预约。
- MQ 最终处理后状态进入 `SUCCESS` 或 `CONFLICT`；`SUCCESS` 携带唯一 `meetingId`。

### 5.4 Agent

```text
POST /api/v1/agent/runs/stream
POST /api/v1/agent/runs/{runId}/resume
GET  /api/v1/agent/runs/{runId}
GET  /api/v1/agent/runs/{runId}/trace
GET  /api/v1/agent/threads
GET  /api/v1/agent/preferences
DELETE /api/v1/agent/preferences
```

启动请求：

```json
{
  "threadId": null,
  "message": "下周三下午帮张三和李四安排一个90分钟架构评审，要大屏",
  "clientRequestId": "uuid"
}
```

Day 3 只实现 Java SSE 代理边界骨架：校验用户 JWT、把请求转发到 Python 最终内部路径并透传标准 SSE 事件；Python 端点不存在或不可用时返回稳定 `AGENT_UNAVAILABLE`，不得由 Java 伪造 Agent 输出。实际 Multi-Agent 流留到 Day 4。

Day 4 固定的 Python→Java→浏览器 SSE 事件契约如下。Java 只校验调用方、签发上下文并逐字节转发；它不生成、修改或解释 Agent 事件。每个 `data` 是单行 JSON，所有摘要均不得包含隐藏推理、令牌或完整敏感正文：

```text
event: run.started
data: {"runId":"run_uuid","threadId":"thread_uuid","traceId":"trc_uuid","status":"RUNNING"}

event: agent.step
data: {"runId":"run_uuid","stepId":"step_uuid","sequenceNo":1,"agentName":"supervisor","nodeName":"supervisor_route","status":"SUCCEEDED","summary":"已路由到需求解析","durationMs":3}

event: tool.call
data: {"runId":"run_uuid","toolCallId":"tool_uuid","toolName":"resolve_employees","riskLevel":"READ","status":"SUCCEEDED","summary":"已解析 2 名员工","durationMs":12}

event: run.completed
data: {"runId":"run_uuid","status":"SUCCEEDED","answerSummary":"已完成结构化解析和只读查询","citations":[]}

event: run.failed
data: {"runId":"run_uuid","status":"FAILED","errorCode":"AGENT_STEP_LIMIT_EXCEEDED","message":"已达到图步骤上限"}
```

- `run.started` 必须最先发送，`run.completed` 或 `run.failed` 必须恰好发送一个作为终止事件。
- `agent.step` 只记录 Agent/确定性节点的结构化业务摘要；`tool.call` 仅记录白名单 Tool 的脱敏参数结果摘要。
- Python 从 Java 传入的 `X-Run-Id`、`X-Trace-Id` 和受服务令牌保护的 Agent Context 中取得运行身份；不得信任请求体伪造的用户身份或运行身份。
- Python 调用 Java Tool 时使用同一 Agent Context、`X-Trace-Id`、`X-Run-Id` 和新生成的 `X-Tool-Call-Id`；Day 4 只允许 READ Tool，禁止调用草案或确认 Tool。

Day 5 在不改变上述事件含义的前提下增加候选、HITL 与异步预约事件。新建 Run 的流仍以 `run.started` 开始；恢复同一 Run 的流以 `run.resumed` 开始。所有 `data` 继续为单行 JSON，候选和草案只包含用户可见的业务字段，绝不包含 Agent Context、JWT、Service Token、隐藏推理或完整 Prompt：

```text
event: run.resumed
data: {"runId":"run_uuid","status":"RUNNING"}

event: plan.candidates
data: {"runId":"run_uuid","candidates":[{"candidateId":"cand_uuid","roomId":101,"roomName":"研发楼301","building":"研发楼","startAt":"2026-08-19T15:00:00+08:00","endAt":"2026-08-19T16:30:00+08:00","totalCost":24,"costBreakdown":{"optionalParticipantConflict":0,"preferredTimeDeviation":0,"buildingDistance":0,"capacityWaste":24,"preferenceViolation":0,"roomChange":0}}]}

event: hitl.required
data: {"runId":"run_uuid","status":"WAITING_CONFIRMATION","confirmationToken":"cfm_uuid","expiresAt":"2026-08-12T10:10:00+08:00","draft":{"title":"架构评审","roomId":101,"roomName":"研发楼301","startAt":"2026-08-19T15:00:00+08:00","endAt":"2026-08-19T16:30:00+08:00","requiredParticipants":[],"optionalParticipants":[],"createVideoConference":false}}

event: booking.pending
data: {"runId":"run_uuid","status":"WAITING_BUSINESS_RESULT","requestNo":"BR202608120001"}

event: booking.completed
data: {"runId":"run_uuid","status":"SUCCESS","meetingId":9001}
```

- `plan.candidates` 最多包含 3 个成本升序且候选 ID 不重复的方案；每个候选都必须先通过 Python 独立硬约束验证器。无解不发送空候选事件，而应以 `run.completed` 返回可解释的无解摘要。
- Scheduling 为成本最低候选调用一次 `create_booking_draft`，再发送 `hitl.required`；Java 创建草案不占用正式会议或槽位。`confirmationToken` 仅可在当前已鉴权用户的 HTTPS/SSE 会话中短暂传递，绝不写入 Trace、日志或持久化摘要。
- `POST /api/v1/agent/runs/{runId}/resume` 的成功响应也是 `text/event-stream`。它只接受 `WAITING_CONFIRMATION` 状态和归属用户（或 ADMIN）；`ACCEPT` 才可调用 `confirm_booking`，`REJECT` 结束且不得调用 WRITE Tool，`EDIT` 仅接受 `roomId` 和/或 `startAt` 后重新进入 Requirement/Scheduling，不得直接确认编辑参数。
- 恢复是一次新的用户 HTTP 动作：Java 为它签发新的请求 `traceId` 与 AgentContext，但 `runId` 必须保持不变，持久化 Run 的初始 `traceId` 不得被覆盖。Python 只校验恢复请求的 Token 与上下文头彼此一致及其用户归属，不能要求该新 `traceId` 等于 Run 的初始 `traceId`；恢复后产生的 Tool 和 HOT `booking_request` 使用当前恢复动作的 `traceId`。
- 一段 SSE 流以 `run.completed`、`run.failed`、`hitl.required` 或 `booking.pending` 之一结束。后两者表示 Run 已安全持久化并暂停，不能再追加 `run.completed` 或 `run.failed`；异步 `BOOKING_RESULT` 通过业务结果回调恢复其状态。
- Day 5 允许 Scheduling 在 READ Tool 完成后调用 `create_booking_draft`；仅在有有效 HITL `ACCEPT` 恢复输入时允许 `confirm_booking`。每次 DRAFT/WRITE Tool 调用继续使用新的 `X-Tool-Call-Id` 与 `(runId, toolCallId, toolName)` 幂等语义。

恢复请求：

```json
{
  "action": "EDIT",
  "confirmationToken": "cfm_uuid",
  "editedDraft": {
    "roomId": 102,
    "startAt": "2026-08-19T15:30:00+08:00"
  },
  "feedback": null
}
```

为完成 HOT `CONFLICT -> DRAFT` 的可恢复闭环，`GET /api/v1/agent/runs/{runId}` 由 Java 以当前请求的 AgentContext 代理同名 Python 内部接口，并使用统一成功信封返回当前用户可见的 Run 恢复视图。响应始终包含脱敏的 Run 元数据；只有该 Run 处于 `WAITING_CONFIRMATION` 且调用者为所属用户或 ADMIN 时，才额外包含 `candidates`、`draft`、`confirmationToken` 和 `expiresAt`。该响应必须设置 `Cache-Control: no-store`，确认令牌不得出现在 `/trace`、日志或持久化摘要中。`GET /api/v1/agent/runs/{runId}/trace` 只代理脱敏 Trace，不包含恢复令牌。

### 5.5 通知

```text
GET   /api/v1/notifications
PATCH /api/v1/notifications/{id}/read
```

## 6. Java内部Tool API

所有请求要求：

```text
Authorization: Bearer <AgentContextToken>
X-Service-Token: <service-token>
X-Trace-Id: <traceId>
X-Run-Id: <runId>
X-Tool-Call-Id: <toolCallId>
```

Day 3 内部安全契约：

- `AgentContextToken` 使用 HS256，固定 `aud=agent-service`，至少包含 `sub`、`roles`、`traceId`、`runId`、`exp`。
- `X-Trace-Id`、`X-Run-Id` 必须与 Token claim 相同，服务端用户上下文只接受 `sub`。
- 所有 Tool 成功结果使用通用成功信封；错误使用通用错误信封。
- `(runId,toolCallId,toolName)` 唯一；同摘要重放历史响应，不同摘要返回 `IDEMPOTENCY_KEY_REUSED`。
- `resolve-employees`、忙闲查询和会议室查询最多返回 50 个对象，时间窗口最大 14 天。

### 6.1 员工解析

```text
POST /internal/v1/tools/resolve-employees
```

```json
{
  "names": ["张三", "李四"],
  "departmentNames": ["支付组"]
}
```

响应数据：

```json
{
  "employees": [
    {
      "employeeId": 1001,
      "username": "zhangsan",
      "displayName": "张三",
      "departmentId": 10,
      "departmentName": "研发中心",
      "status": "ACTIVE"
    }
  ],
  "unresolvedNames": []
}
```

### 6.2 忙闲查询

```text
POST /internal/v1/tools/get-employee-free-busy
```

```json
{
  "employeeIds": [1001, 1002],
  "from": "2026-08-19T13:00:00+08:00",
  "to": "2026-08-19T18:00:00+08:00"
}
```

响应数据按员工分组，只返回正式 REQUIRED 忙碌槽位：

```json
{
  "employees": [
    {
      "employeeId": 1001,
      "busySlots": [
        {
          "meetingId": 9001,
          "startAt": "2026-08-19T15:00:00+08:00",
          "endAt": "2026-08-19T15:30:00+08:00"
        }
      ]
    }
  ]
}
```

### 6.3 会议室查询

```text
POST /internal/v1/tools/search-available-rooms
```

```json
{
  "from": "2026-08-19T13:00:00+08:00",
  "to": "2026-08-19T18:00:00+08:00",
  "minimumCapacity": 10,
  "requiredFeatures": ["LARGE_SCREEN"],
  "limit": 50
}
```

响应数据为整个请求区间均可用且满足容量/设备的会议室数组：

```json
{
  "rooms": [
    {
      "roomId": 102,
      "roomCode": "RD-302",
      "roomName": "研发楼 302",
      "building": "研发楼",
      "floor": "3F",
      "capacity": 16,
      "roomType": "STANDARD",
      "isHot": false,
      "features": ["WHITEBOARD", "LARGE_SCREEN", "PROJECTOR"]
    }
  ]
}
```

### 6.4 最近会议

```text
POST /internal/v1/tools/get-recent-meeting
```

```json
{"limit": 5}
```

响应数据为 `{"meetings":[MeetingView...]}`，包含当前 Token 用户可见、按 `startAt` 倒序的最多 5 条 MeetingView；服务端不接受调用方伪造 `userId`。

### 6.5 草案工具

```text
POST /internal/v1/tools/booking-drafts
POST /internal/v1/tools/reschedule-drafts
POST /internal/v1/tools/cancellation-previews
```

创建草案响应：

```json
{
  "confirmationToken": "cfm_uuid",
  "expiresAt": "2026-08-12T10:10:00+08:00",
  "draft": {
    "title": "架构评审",
    "roomId": 101,
    "roomName": "研发楼301",
    "startAt": "2026-08-19T15:00:00+08:00",
    "endAt": "2026-08-19T16:30:00+08:00",
    "requiredParticipants": [],
    "optionalParticipants": [],
    "createVideoConference": false
  }
}
```

`POST /internal/v1/tools/booking-drafts` 的请求体与 5.3 节手动创建业务字段相同；`runId/toolCallId/userId` 分别来自内部头和 AgentContextToken。创建草案只写 `booking_draft(PENDING)`，不得写 `meeting` 或正式槽位。确认令牌默认 10 分钟过期。

- `POST /internal/v1/tools/reschedule-drafts` 请求体为 `meetingId` 加 5.3 节修改请求的全部字段，响应 draft 同时包含原会议摘要和 proposedMeeting；只允许发起人或 ADMIN 创建。
- `POST /internal/v1/tools/cancellation-previews` 请求体为 `{"meetingId":9001}`，响应 draft 包含待取消 MeetingView。
- 三类草案在确认前都不得修改正式会议、参与者或槽位。

### 6.6 确认工具

```text
POST /internal/v1/tools/booking-drafts/{confirmationToken}/confirm
POST /internal/v1/tools/reschedule-drafts/{confirmationToken}/confirm
POST /internal/v1/tools/cancellation-previews/{confirmationToken}/confirm
POST /internal/v1/tools/meetings/{meetingId}/video-conference
```

确认响应可能为：

```json
{
  "status": "SUCCESS",
  "meetingId": 9001,
  "requestNo": null
}
```

或：

```json
{
  "status": "PENDING",
  "meetingId": null,
  "requestNo": "BR202608120001"
}
```

- 确认请求必须携带 `Idempotency-Key`；令牌仅允许所属用户使用一次。
- 非 HOT 草案复用 Day 2 同一最终校验/事务服务，返回 HTTP 200 `SUCCESS + meetingId`。
- 改期和取消草案确认在 Day 3 复用 Day 2 update/cancel 事务并同步返回 `SUCCESS + meetingId`；重复 Tool 调用返回首次结果。
- HOT 草案在一个事务中写入 `booking_request(PENDING)`、`message_outbox(BOOKING_COMMAND)`、幂等记录并将草案标记 `USED`，返回 HTTP 202 `PENDING + requestNo`；受理事务不创建 meeting/slot。
- `BOOKING_COMMAND` 消费者以 `eventId` 和 booking request 业务终态幂等；SUCCESS/CONFLICT 都必须写 `BOOKING_RESULT` Outbox。
- Day 3 的 `BOOKING_RESULT` 到 Python 回调适配器曾默认由 `AGENT_CALLBACK_ENABLED=false` 禁用；Day 5 已接入第 7 节 `business-result` API，当前 Compose 和 Java 默认值为 `true`，测试 profile 仍可显式关闭。

## 7. Python内部API

```text
POST /internal/v1/agent-runs/stream
POST /internal/v1/agent-runs/{runId}/resume
POST /internal/v1/agent-runs/{runId}/business-result
GET  /internal/v1/agent-runs/{runId}
GET  /internal/v1/agent-runs/{runId}/trace
GET  /internal/v1/health
```

除健康检查外，Day 4 的 Agent Run 内部接口只接受 Java 代理调用，并统一要求：

```text
Authorization: Bearer <AgentContextToken>
X-Service-Token: <service-token>
X-Trace-Id: <traceId>
X-Run-Id: <runId>
```

Python 必须校验 HS256 `AgentContextToken` 的签名、`aud=agent-service`、过期时间以及 `sub/roles/traceId/runId`；两个上下文头必须与 claim 完全一致。请求体中的用户、角色、Trace 或 Run 字段不可信。`GET /agent-runs/{runId}` 和 `/trace` 仅允许 Token `sub` 对应的 Run 所有者访问，或允许 `ADMIN` 访问。

`GET /internal/v1/agent-runs/{runId}/trace` 返回持久化的、脱敏的元数据（不使用公共 API 信封）：

```json
{
  "run": {
    "runId": "run_uuid",
    "threadId": "thread_uuid",
    "traceId": "trc_uuid",
    "userId": 1001,
    "intent": "CREATE_MEETING",
    "status": "SUCCEEDED",
    "questionSummary": "用户提交架构评审任务（正文长度=28）",
    "answerSummary": "已完成结构化解析和只读查询",
    "modelCallCount": 3,
    "toolCallCount": 1,
    "durationMs": 42,
    "errorCode": null,
    "createdAt": "2026-08-12T10:00:00+08:00",
    "finishedAt": "2026-08-12T10:00:00+08:00"
  },
  "steps": [
    {
      "stepId": "step_uuid",
      "sequenceNo": 1,
      "agentName": "supervisor",
      "nodeName": "supervisor_route",
      "status": "SUCCEEDED",
      "summary": "已路由到需求解析",
      "durationMs": 3,
      "errorCode": null,
      "createdAt": "2026-08-12T10:00:00+08:00"
    }
  ],
  "toolCalls": [
    {
      "toolCallId": "tool_uuid",
      "toolName": "resolve_employees",
      "riskLevel": "READ",
      "sanitizedArgs": {"nameCount": 2},
      "resultSummary": "已解析 2 名员工",
      "status": "SUCCEEDED",
      "durationMs": 12,
      "createdAt": "2026-08-12T10:00:00+08:00"
    }
  ]
}
```

Day 4 只允许 Stream 内部调用 Java READ Tool；`resume`、`business-result`、草案和确认 Tool 的业务行为仍保留到 Day 5，不能用 Day 4 Stub 提前伪造。

Day 5 的 `resume` 与 `business-result` 使用与 Stream 完全相同的 Java AgentContext、Service Token、`X-Trace-Id` 和 `X-Run-Id` 校验。`business-result` 回调由 Java 结果消费者在数据库事务提交后发起；Java 必须以该 `booking_request` 所属用户的当前角色、事件原始 `traceId` 和 `runId` 签发 AgentContext。Python 以 `eventId` 去重，并且只在 checkpoint 为 `WAITING_BUSINESS_RESULT` 且 `requestNo` 相同时处理；已结束或不匹配的 Run 返回 2xx 的忽略结果，避免 RocketMQ 重复投递造成无限重试。

业务结果回调：

```json
{
  "eventId": "evt_uuid",
  "requestNo": "BR202608120001",
  "status": "CONFLICT",
  "meetingId": null,
  "conflict": {
    "type": "ROOM_SLOT",
    "roomId": 101,
    "slots": [30, 31, 32]
  }
}
```

## 8. 视频会议Mock API

```text
POST   /mock/v1/video-meetings
PUT    /mock/v1/video-meetings/{externalMeetingId}
DELETE /mock/v1/video-meetings/{externalMeetingId}
GET    /mock/v1/video-meetings/{externalMeetingId}
GET    /health
```

创建请求必须包含 `Idempotency-Key`，重复请求返回相同链接。

## 9. 分页与上限

- 默认页大小20，最大100。
- Agent忙闲查询最多50人。
- Agent会议室查询最多50间。
- 单次时间窗口最大14天。
- 单个会议最大100名参与者。
- Tool结果正文默认不超过32KB，超出返回摘要和结果ID。
