# 04. Multi-Agent 规范

## 0. Spec 1.1：受控 Agent Loop 升级

本节覆盖后续章节中与一次性固定 Tool 计划冲突的旧描述。运行时 Agent 数量仍固定为 Supervisor、Requirement、Policy、Scheduling；Evaluator、Tool Executor、Verifier、Solver、HITL 和 Conflict Repair Handler 均为确定性组件，不新增 Critic Agent，也不引入 DeepAgents。

### 0.1 主循环

Scheduling Agent 使用有界 `PLAN -> ACT -> OBSERVE -> VERIFY -> SOLVE/REPLAN`：

1. PLAN：DeepSeek 通过 OpenAI-compatible `tools` 选择 Java READ Tool，并生成符合 JSON Schema 的参数。
2. ACT：Tool Gate 校验名称、Pydantic 参数、AgentContext 派生的用户身份、时间/人数上限、风险等级和调用指纹；模型不得提供可信 userId、runId 或权限。
3. OBSERVE：只把脱敏、限量的 Tool 结果作为 `role=tool` 消息返回模型，并将结构化摘要写 Trace。
4. VERIFY：确定性 Evaluator 检查所需事实是否齐备、调用是否重复、参数是否偏离 canonical requirement、预算是否耗尽；不通过时把结构化 feedback 返回同一 Scheduling Agent。
5. SOLVE：事实齐备后由 OR-Tools 求解，结果必须再经过独立 HardConstraintValidator。
6. REPLAN：Tool 校验失败、可恢复 Tool 错误、无效计划或 Java 最终冲突可触发重规划；不得绕过验证直接创建业务记录。

普通 `https://api.deepseek.com` 端点使用原生 Tool Calling，但不发送 Beta 专属的 `function.strict=true`；严格性由本地 Pydantic `extra=forbid`、canonical context 和 Tool Gate 保证。只有显式将 Base URL 配为 `/beta` 并完成兼容测试后，才允许启用服务端 strict mode。Tool Loop 显式设置 `thinking.type=disabled`，避免 V4 默认思考模式要求跨轮回传 `reasoning_content`；本系统不存储或展示该隐藏推理。默认示例模型使用当前仍受支持的 `deepseek-v4-flash`，实际模型名始终由环境变量注入。

### 0.2 预算与停止条件

- 单次 Scheduling Tool Loop 最多4轮；同一 `(toolName, canonicalArgsHash)` 不得重复执行。
- 全 Run 最多12次模型调用、16次 Tool 调用、2次业务冲突重规划；Schema 修复和 Evaluator 修复均计入模型调用，该预算允许初始规划加两次完整的事实刷新，沿用图节点上限作为最后保险。达到预算统一返回稳定 `BUDGET_EXHAUSTED`，而不是笼统内部错误。
- 预算终态固定为 `READY_FOR_CONFIRMATION`、`NEED_CLARIFICATION`、`NO_SOLUTION`、`BUDGET_EXHAUSTED`、`TOOL_UNAVAILABLE`、`WAITING_BUSINESS_RESULT`、`COMPLETED`、`FAILED`。
- Loop 到达 `READY_FOR_CONFIRMATION` 后才允许确定性节点创建无占用草案；确认写 Tool 只在 HITL ACCEPT 后暴露给确定性确认节点。

### 0.3 Requirement Evaluator-Optimizer

- 第一次 Requirement 输出仍由 Pydantic 校验；Schema 修复重试和语义优化重试分别最多1次，但同一次错误不得触发无界嵌套重试。
- 语义 Evaluator 至少检查：服务端注入的 `requestTime`、Asia/Shanghai 相对日期基准、30分钟槽位、持续时间、必需参会者、容量与人员数一致性、修改/取消 targetMeetingId、硬软约束冲突。
- 可自动修复的问题以 `RequirementFeedback{codes,summary}` 返回同一 Requirement Agent；必须由用户提供的信息进入 `WAITING_USER_INPUT`，禁止模型猜测。

### 0.4 并发冲突修复

- Java `BOOKING_RESULT.CONFLICT` 仍是最终事实；Python 从回调、失败草案和原始约束派生 `ConflictRepairFeedback`。
- Feedback 至少包含 conflictType、failedCandidateId、preservedConstraints、excludedCandidateIds、replanCount 和可向用户展示的变更原因。
- 重规划必须重新读取 Java 忙闲/房间事实、排除失败候选、保留硬约束并重新经过 OR-Tools 与独立 Validator。
- 只有候选或约束发生真实变化才生成新草案；重复候选、超过2次冲突或必须放宽硬约束时停止并请求用户决策。

### 0.5 Trace 与评测

- Trace 可记录 loop phase、iteration、decision、tool name、参数摘要、observation 摘要、feedback code、replan count、stop reason、剩余预算、模型/Prompt/Schema 版本和 API 返回的 Token usage；不得记录隐藏推理或秘密。
- Fixture 评测只能命名为组件/回归评测。`E2E Task Success` 必须实际运行完整 Graph；真实模型报告必须单独标注 provider/model、重复次数、网络调用、延迟、Token/成本和失败样本。

### 0.6 澄清与用户可见错误

- Evaluator、Tool Gate、Java Tool 和 OR-Tools 只负责确定问题与可信事实；内部错误码只进入 Trace 和日志，不得直接拼接到用户回答。
- 需要用户补充信息时，确定性组件先把问题映射为 `explanation + requestedInput + verifiedFacts`。Supervisor 只能基于该结构和原始请求生成自然语言澄清，不得新增事实、放宽约束或声称已经产生业务写入。
- Supervisor 的澄清输出必须经过 Pydantic、内部错误码泄露、未验证写入结论和数字/时间来源校验；模型不可用或输出未通过校验时，使用确定性中文模板，不得把表达失败升级为整个 Run 失败。
- `timeWindow` 表示候选方案的可搜索窗口，`durationMinutes` 表示单场会议时长。用户同时给出“13:00 到 18:00 之间”和“60 分钟”时，两者必须独立保留；只有明确表达固定起止时间且没有另给时长时，才允许从起止时间推导时长。
- 人员无法唯一识别、关键信息缺失、人员共同空闲冲突、会议室/设备无解和并发重规划耗尽都必须使用普通业务语言说明，并给出下一步可执行选择；不得要求非专业用户理解内部协议或错误枚举。

### 0.7 多轮需求槽位收敛

- Requirement 首轮输出和后续补充都先形成 `RequirementDraft`；只有时间窗口、会议时长和必需参会范围全部达到可执行状态后，才物化完整 `MeetingRequest` 并进入 Scheduling。禁止用30分钟或仅发起人占位绕过缺失校验。
- 每个关键槽位记录 `EXPLICIT|DEFAULTED|DIRECTORY_RESOLVED|MISSING|AMBIGUOUS|CONFLICT`、来源文本、规则标识和 revision；非刚需可选项额外使用 `UNSPECIFIED|CLOSED`。最新用户明确值覆盖历史默认/通讯录推定；旧的明确值在用户未修改时继续保留。
- 日期计算、时段映射、跨午夜、30分钟槽位、过去时间检查和“最好/必须”软硬语义由确定性 Normalizer/Evaluator 裁决；LLM 只抽取原始表达与意图。
- “我的小组/同组人员”先提取为人员范围，不允许模型生成姓名。确定性节点调用 Java `resolve_participant_scope`，由 Java 从 AgentContext 的用户身份解析所属部门和 ACTIVE 名单；单一结果可以作为可纠正的 `DIRECTORY_RESOLVED`，无部门、空部门或多义范围进入澄清。
- “我和李四”“我跟李四”“包括我”“我必须参加”等明确参会表达中的第一人称，由确定性节点映射为当前已鉴权登录用户；模型不得生成用户姓名或 ID。需求摘要显示“当前登录用户（我）”，排期、忙闲校验与草案写入使用 AgentContext 的 userId，确保该用户与显式姓名共同成为必需参会者。
- 澄清计划固定包含 `verifiedFacts + appliedDefaults + directoryAssumptions + blockingQuestions + optionalPrompt`。可选设备/地点没有回复时采用无硬性要求，不得反复阻塞。
- `WAITING_USER_INPUT` checkpoint 保留部分 Draft、槽位来源、revision 和已验证通讯录结果。`POST /agent-runs/{runId}/input` 在运行锁内校验归属、状态、revision 和 `clientRequestId`，合并新一轮 `RequirementDelta` 后从 Requirement 重新执行；同一 Run 的模型/Tool/图预算继续累计。
- 人员修改必须先确定 ADD/REMOVE/REPLACE，再作用于上一版已验证名单；REMOVE 只允许删除旧名单中被用户点名且带删除语义的人员。人员变化后必须同步过滤 `resolvedEmployees`、重算容量并重新验证新增姓名。
- FAILED Run 的普通继续输入创建新 Run，并通过显式 `baseRunId` 继承最后有效 Requirement 基线。继承仅允许同用户、同 thread 的 FAILED Run；所有运行预算、候选、草案、HITL 令牌、业务结果和工具幂等状态必须重置。

## 1. 目标

Agent服务负责把自然语言会议任务转换为可验证、可确认、可执行的业务动作。它不拥有会议业务事实，也不直接写业务数据库。

系统固定为四个Agent：

1. Supervisor Agent。
2. Requirement Agent。
3. Policy Agent。
4. Scheduling Agent。

工具、OR-Tools、RAG Retriever和HITL Handler是确定性节点，不额外包装成Agent。

## 2. Agent职责

### 2.1 Supervisor Agent

职责：

- 识别工作流阶段和下一执行节点。
- 根据任务决定是否需要Policy Agent和Scheduling Agent。
- 控制最大迭代次数。
- 发现缺失信息时生成澄清问题。
- 汇总专业Agent结果。
- 决定进入HITL、等待异步结果或结束。

允许的路由：

```text
REQUIREMENT
POLICY
SCHEDULING
CLARIFICATION
HITL
WAIT_BUSINESS_RESULT
FINAL
FAIL
```

Supervisor不得直接调用业务写工具。

### 2.2 Requirement Agent

职责：

- 意图识别。
- 相对日期和时间窗口规范化。
- 会议时长、参与者、房间和设备提取。
- REQUIRED与OPTIONAL参与者划分。
- 硬约束与软约束划分。
- “刚才那个会议”等指代解析。
- 显式偏好更新识别。
- 对不完整请求生成结构化缺失字段。

输出必须符合 `MeetingRequest` Schema，不允许直接输出预约结论。

Requirement Agent 额外支持隔离的 `POST_MEETING_ANALYSIS` 结构化抽取模式：输入只能是 Java 已鉴权并限长的会议标题、类型、时间、参与者白名单和用户提交的文本记录；输出为 `PostMeetingDraft`，包含纪要、决策和行动项草案。该模式不进入 Scheduling、不调用 Java Tool、不写正式业务表，也不构成第五个运行时 Agent。

### 2.3 Policy Agent

职责：

- 将用户问题和会议类型转换为检索查询。
- 调用简化RAG。
- 选择支持答案的证据。
- 输出规则摘要、引用和置信度。
- 将可识别规则转换为有限枚举约束。

允许转换的规则类型：

```text
MAX_DURATION_MINUTES
ALLOWED_ROOM_TYPES
REQUIRED_ROOM_FEATURE
DISALLOWED_TIME_WINDOW
ADVISORY_ONLY
```

RAG产生的规则不能绕过Java最终校验。证据不足时标记 `UNVERIFIED`。

RAG 语料导入是确定性基础设施组件，不是新的 Agent：

- 只接受会议制度与会议规范的 UTF-8 Markdown 和文本型 PDF；不做 OCR、Rerank 或知识图谱。
- 按标题路径切片并保留 documentId、chunkId、标题、页码、版本、优先级和 checksum，Policy Agent 只能引用本轮真实召回且能重新打开的 chunk。
- 文件导入失败不得用内置模型知识补齐；检索无可验证依据时必须回答“未找到可验证证据”并标记 `UNVERIFIED`。
- 文档索引最终一致，不改变 Java 业务事实、预约权限或硬约束；Java 写入前仍重新校验全部业务规则。
- 在线管理由受 Service Token 与 AgentContext 保护的 Python 内部资源 API 执行；所有用户可读，写入仅 ADMIN。删除保留 `DELETED` tombstone，阻止部署期种子目录静默恢复；管理员显式重传同一 `documentId` 才可恢复。

### 2.4 Scheduling Agent

职责：

- 决定调用哪些忙闲和会议室查询工具。
- 把Requirement和Policy结果转换为OR-Tools输入。
- 对求解结果进行业务语义解释。
- 处理无解原因和约束松弛建议。
- 创建预约、改期或取消草案。
- 热门预约冲突恢复后重新规划。

Scheduling Agent不能自行修改其他用户会议。

## 3. LangGraph结构

```mermaid
flowchart TD
    START --> SUP[supervisor]
    SUP -->|parse/update context| REQ[requirement_agent]
    REQ --> SUP
    SUP -->|policy needed| POL[policy_agent]
    POL --> SUP
    SUP -->|schedule needed| PRE[load_business_context]
    PRE --> SCH[scheduling_agent]
    SCH --> SOLVE[constraint_solver]
    SOLVE --> SCH
    SCH --> DRAFT[create_draft_tool]
    DRAFT --> INTERRUPT[HITL interrupt]
    INTERRUPT -->|edit/feedback| SUP
    INTERRUPT -->|accept| WRITE[confirmed_write_tool]
    WRITE -->|PENDING| WAIT[wait_business_result]
    WAIT -->|SUCCESS| POST[post_booking_actions]
    WAIT -->|CONFLICT| SCH
    WRITE -->|SUCCESS| POST
    POST --> FINAL[compose_final]
    SUP -->|policy only| FINAL
    SUP -->|missing input| ASK[clarification]
    ASK --> END
    FINAL --> END
```

实现要求：

- 每个专业Agent使用独立system prompt和结构化输出Schema。
- 每个专业Agent是独立LangGraph节点或子图。
- Agent间只通过共享State中的结构化字段交接。
- 不依赖自然语言消息作为唯一内部协议。
- 每个Run最多12次模型调用、16次工具调用和20个图节点。
- 超限后返回 `AGENT_STEP_LIMIT_EXCEEDED`。

## 4. 共享状态

建议Pydantic模型：

```python
class MeetingAgentState(BaseModel):
    thread_id: str
    run_id: str
    trace_id: str
    user_id: int
    messages: list[Message]
    intent: Intent | None
    meeting_request: MeetingRequest | None
    missing_fields: list[str] = []
    policy_result: PolicyResult | None
    employee_context: EmployeeContext | None
    availability_snapshot: AvailabilitySnapshot | None
    schedule_candidates: list[ScheduleCandidate] = []
    selected_candidate_id: str | None
    draft: BookingDraftView | None
    confirmation_token: str | None
    pending_request_no: str | None
    business_result: BusinessResult | None
    user_preferences: SchedulingPreferences | None
    citations: list[Citation] = []
    step_count: int = 0
    tool_call_count: int = 0
    status: RunStatus
    error: AgentError | None
```

关键枚举：

```text
Intent:
CREATE_MEETING
FIND_COMMON_TIME
RECOMMEND_ROOM
MODIFY_MEETING
CANCEL_MEETING
QUERY_POLICY
UPDATE_PREFERENCE

RunStatus:
RUNNING
WAITING_USER_INPUT
WAITING_CONFIRMATION
WAITING_BUSINESS_RESULT
SUCCEEDED
FAILED
CANCELLED
```

## 5. MeetingRequest Schema

```json
{
  "intent": "CREATE_MEETING",
  "title": "架构评审",
  "meetingType": "ARCHITECTURE_REVIEW",
  "durationMinutes": 90,
  "timeWindow": {
    "start": "2026-08-19T13:00:00+08:00",
    "end": "2026-08-19T18:00:00+08:00"
  },
  "requiredParticipants": [
    {"name": "王经理", "employeeId": null}
  ],
  "optionalGroups": ["支付组", "订单组"],
  "requiredFeatures": ["LARGE_SCREEN"],
  "minimumCapacity": null,
  "preferredBuildings": [],
  "hardConstraints": [
    {"type": "END_BEFORE", "value": "18:00"}
  ],
  "softConstraints": [
    {"type": "START_AFTER", "value": "15:00", "weight": 20}
  ],
  "targetMeetingId": null
}
```

所有日期必须转换为绝对时间后再调用业务工具。

## 6. Tool目录

### 6.1 Java业务工具

| Tool | 风险 | 直接执行 | 说明 |
|---|---|---:|---|
| resolve_employees | READ | 是 | 姓名、部门解析 |
| get_employee_free_busy | READ | 是 | 忙碌槽位 |
| search_available_rooms | READ | 是 | 房间和空闲槽位 |
| get_recent_meeting | READ | 是 | 上下文会议解析 |
| create_booking_draft | DRAFT | 是 | 创建无业务占用草案 |
| create_reschedule_draft | DRAFT | 是 | 创建变更草案 |
| create_cancellation_preview | DRAFT | 是 | 取消预检 |
| confirm_booking | WRITE | 否 | 必须经过HITL |
| confirm_reschedule | WRITE | 否 | 必须经过HITL |
| confirm_cancellation | WRITE | 否 | 必须经过HITL |

### 6.2 Python内部工具

| Tool | 说明 |
|---|---|
| search_meeting_policy | Qdrant检索会议制度 |
| open_policy_chunks | 打开本轮候选chunk正文 |
| solve_schedule | OR-Tools求解Top K方案 |
| save_explicit_preference | 保存用户明确表达的偏好 |

### 6.3 Tool Schema规则

- 所有工具参数使用Pydantic模型。
- `additionalProperties`逻辑上禁止。
- ID、数组长度、时间跨度和返回条数必须设上限。
- 模型输出参数解析失败时最多修复重试1次。
- Tool结果返回结构化摘要，不把大对象全部写入消息历史。
- Tool异常转换为有限错误码，由Agent决定重试、追问或终止。

## 7. DeepSeek适配

### 7.1 模型角色

- Supervisor：结构化路由，temperature 0。
- Requirement：结构化抽取，temperature 0。
- Policy：证据选择和回答生成，低temperature。
- Scheduling：工具选择和方案解释，低temperature。

### 7.2 调用规则

- 使用OpenAI-compatible接口封装在 `ModelProvider` 中。
- 模型名、Base URL、API Key和超时通过环境变量配置。
- 不在业务代码中硬编码具体模型版本。
- 对Tool参数和结构化输出执行Pydantic校验。
- JSON空响应、截断、429和5xx使用有限重试。
- 不保存或展示模型隐藏推理内容；Trace只记录路由理由摘要。
- API Key只存在于Agent服务环境变量。

## 8. HITL与Checkpoint

### 8.1 暂停点

- 预约确认前。
- 改期确认前。
- 取消确认前。
- Agent需要关键缺失信息时。

### 8.2 持久化

- 使用Redis持久化LangGraph checkpoint，并启用AOF卷。
- checkpoint key至少包含 `threadId + runId`。
- Run默认保留24小时。
- BookingDraft确认令牌默认保留10分钟。
- 恢复时重新加载当前权限、偏好和业务状态。

### 8.3 Resume输入

```json
{
  "action": "ACCEPT",
  "confirmationToken": "cfm_uuid",
  "editedDraft": null,
  "feedback": null
}
```

`EDIT`后必须重新进入Requirement或Scheduling流程，不能直接执行编辑后的参数。

### 8.4 会后草案审核

- 会后分析由 Java 同步调用 `POST /internal/v1/post-meeting/drafts`，Python 使用现有 Provider 与 `StructuredModelRunner`，Schema 修复最多 1 次。
- `PostMeetingDraft` 最多包含 20 条决策和 50 条行动项；正文和单字段均有长度上限。
- 模型只能从输入白名单选择 `assigneeEmployeeId`。无法唯一识别负责人时返回 `null`，不得猜测或创造业务 ID。
- Python 返回内容始终是 DRAFT。`ACCEPT/EDIT/REJECT` 由 Java 公共业务接口处理；`EDIT` 后仍需再次 `ACCEPT`，Python 不获得 WRITE Tool。
- Provider 网络失败、空输出、Schema 失败或白名单违规返回稳定内部错误，不得生成恒定成功草案。

## 9. 热门预约结果恢复

Python内部回调：

```text
POST /internal/v1/agent-runs/{runId}/business-result
```

处理步骤：

1. 验证Java服务签名。
2. 以 `eventId` 检查回调幂等。
3. 加载runId checkpoint。
4. 验证当前状态为 `WAITING_BUSINESS_RESULT` 且requestNo一致。
5. 写入BusinessResult。
6. SUCCESS进入后置动作和最终回答。
7. CONFLICT清除旧候选，回到Scheduling Agent。
8. 更新Agent Run和回调幂等记录。

若run已结束，回调返回2xx并记录忽略原因，防止MQ无限重试。

## 10. OR-Tools模型

### 10.1 候选集合

Java先返回指定窗口内：

- 房间基本信息。
- 房间正式忙碌槽位（带可公开的 meetingId）；Python 据此计算连续可用槽位。
- REQUIRED和OPTIONAL参与者忙碌槽位。

Python生成候选 `(room, startSlot)`，会议持续槽位数：

```text
slotCount = durationMinutes / 30
```

### 10.2 决策变量

对每个候选建立布尔变量：

```text
x(room, startSlot) ∈ {0, 1}
```

单次方案要求：

```text
Σ x(room, startSlot) = 1
```

### 10.3 硬约束

- 起止时间在用户窗口内。
- 会议占用的全部连续槽位可用。
- 所有REQUIRED参与者在全部槽位可用。
- 房间容量满足人数。
- 房间包含全部必需设备。
- 满足可机器执行的政策约束。

不满足硬约束的候选在建模前直接过滤。

### 10.4 软约束目标

最小化：

```text
totalCost =
  optionalParticipantConflictCount * 100
  + preferredTimeDeviationSlots * 20
  + buildingDistanceScore * 10
  + capacityWaste * 2
  + userPreferenceViolation * 15
  + roomChangeCost * 30
```

权重集中配置并记录到Trace，便于解释和评测。

### 10.5 Top 3方案

- 求得最优方案后记录结果。
- 增加排除该候选的no-good约束。
- 最多重复3次。
- 返回每个方案的总成本和分项成本。

### 10.6 无解处理

按以下顺序分析：

1. 检查会议室设备/容量是否导致候选为空。
2. 检查REQUIRED参与者共同空闲。
3. 检查时间窗口和持续时长。
4. 检查政策硬约束。
5. 生成有限的松弛建议，不自动应用。

`UnsatAnalysis` 不是固定文案，而是前后端共同使用的结构化结果：

```json
{
  "category": "REQUIRED_AVAILABILITY",
  "summary": "2026-08-27 14:00-16:00 无法安排：李四在 14:00-15:30 已有会议（meetingId=123）。",
  "requestedWindow": {
    "start": "2026-08-27T14:00:00+08:00",
    "end": "2026-08-27T16:00:00+08:00"
  },
  "durationMinutes": 120,
  "blockingIntervals": [
    {
      "resourceType": "EMPLOYEE",
      "resourceId": 1003,
      "resourceName": "李四",
      "meetingId": 123,
      "start": "2026-08-27T14:00:00+08:00",
      "end": "2026-08-27T15:30:00+08:00",
      "reason": "必需参会者已有会议"
    }
  ],
  "relaxationSuggestions": ["延长时间窗口", "调整开始时间"]
}
```

- `blockingIntervals` 最多 10 条，仅披露当前用户有权看到的员工显示名、时间与会议 ID，不披露其他会议标题或参会名单。每一条用户可见说明必须明确成对关系：本次待排请求（请求窗口和时长）与哪位必需参会者或哪间会议室的已有安排冲突，并展示实际重叠时段；不得只罗列“存在冲突”。
- `requestedWindow` 和 `durationMinutes` 必须来自本次已验证请求，不能引用用于选择目标会议的旧时间。
- 无解时发送 `plan.unsat`，并以 `WAITING_USER_INPUT` 保留当前 checkpoint；恢复接口保留同一 `unsatAnalysis`，`run.completed.answerSummary` 使用该分析的可读摘要。用户明确接受分析中的建议时间后，从同一 Run 重新校验忙闲、会议室和全部硬约束，不能把“建议”直接当作已验证候选。

### 10.7 改期目标解析与字段继承

改期与取消先调用 `get_recent_meeting` 读取当前用户可管理的候选会议，再执行任何忙闲、房间、草案或写 Tool。确定性解析器按以下优先级唯一选择目标：

1. 已有 `targetMeetingId`；
2. 用户明确给出的目标日期与开始时刻；
3. 用户明确给出的标题片段；
4. “刚才/最近一场”等相对指代。

零个或多个匹配均进入 `WAITING_USER_INPUT` 并展示有界候选摘要，不得默认取列表第一项。唯一命中后，以 Java `MeetingView` 作为原会议事实：

- `pendingStartAt` 或明确的新时间只生成目标窗口，不能复用旧目标选择窗口；
- 未明确修改时，时长由原会议 `endAt-startAt` 推导，人员、标题、类型保持不变；
- “设备不变”保留原房间设备要求，其他字段只接受用户明确增量；
- `get_free_busy` 与 `search_available_rooms` 由 Tool Gate 注入同一 `excludeMeetingId`；Java 验证当前用户确实可管理该会议后，只排除该会议产生的占用。

### 10.8 异常重排对话

- Supervisor 将“异常重排”“资源失效”“会议室不可用”稳定映射为 `RESCHEDULE`；异常单号只用于用户可见关联，目标会议仍必须由 Java 可管理会议事实中的显式 meetingId 唯一命中。
- 默认继承原会议时间、时长、必需/可选参会人和设备要求，失效房间加入排除项。先尝试原时段，不能把失效房间或 INACTIVE 房间返回为候选。
- 用户未明确同意前不得放宽任何硬约束。用户后续提出“顺延 30 分钟”“不要求白板”“换楼”等变化时，Requirement 必须把改变项标为 `EXPLICIT`、未变项标为 `INHERITED`，并重新执行事实读取、OR-Tools 求解和独立验证。
- 无解时沿用 `UnsatAnalysis` 和 `WAITING_USER_INPUT`；选中方案后沿用 RESCHEDULE Before/After 草案及 `ACCEPT/EDIT/REJECT`。REJECT 不关闭 Java 异常单，ACCEPT 后由 Java 会议事务自动关闭。

## 11. 简化RAG

### 11.1 文档范围

- 会议室管理制度。
- VIP会议室使用规则。
- 访客和保密会议规定。
- 取消/改期规则。
- 架构评审、需求评审、Kickoff、复盘和客户会议规范。
- 视频、投影、白板设备说明。
- 部门规范和FAQ。

### 11.2 入库

- 首版支持Markdown、文本型PDF。
- 通过CLI或管理员接口触发。
- 按标题层级切片，超长章节按段落拆分，正文上限 1200 字符；完整正文仍保存在 `rag_document.content_text` 供知识库页面浏览。
- 生产检索统一使用只读挂载的本地 BGE-M3 稠密向量（1024 维、归一化），部署初始化、管理员上传/编辑和 Policy 查询复用同一进程缓存的 Embedding Provider；常驻 API 在启动期预热，避免首个用户请求承担模型冷启动。单元测试可显式选择确定性 fixture provider。
- “RAG 测试问题”及其子章节只用于人工/后续评测，不进入 Qdrant；不做 OCR、稀疏混合检索、ColBERT 或 Rerank。
- 管理员在线上传最大 5 MiB；Markdown 可在线编辑，文本型 PDF 只允许查看提取正文并通过重新上传替换。
- 编辑、替换和恢复都按 documentId 全量重建 chunks；删除先清理 Qdrant points，再写 tombstone。

### 11.3 元数据

```json
{
  "documentId": "doc_uuid",
  "title": "架构评审规范",
  "documentType": "MEETING_STANDARD",
  "department": "研发部",
  "version": "1.0",
  "effectiveDate": "2026-01-01",
  "headingPath": ["架构评审", "参会要求"],
  "page": 2,
  "chunkId": "chunk_uuid"
}
```

### 11.4 检索

采用两阶段读取：

1. `search_meeting_policy`以 BGE-M3 dense 向量召回并施加轻量标题/正文词项加分，返回Top 5候选摘要和chunkId。
2. `open_policy_chunks`只允许打开本轮候选中的Top 2至3个正文。

生产集合固定使用版本化新名称 `meeting_policies_bge_m3_v1`，不原地改写旧 64 维集合；运行时不自动注入内置 seed，4 条 `SEED_CHUNKS` 只供 `InMemoryPolicyRetriever` 测试使用。

回答必须带文档名、标题路径和页码或chunkId。

## 12. 用户偏好

只保存明确表达的偏好：

```json
{
  "preferredBuildings": ["研发楼"],
  "avoidWeekdays": ["FRIDAY"],
  "avoidTimeRanges": [{"start": "13:00", "end": "18:00"}],
  "preferredTimeRanges": [],
  "updatedFromRunId": "run_uuid"
}
```

- 不从行为历史自动推断。
- 用户可以查看和删除。
- 偏好都是软约束。

## 13. Trace规范

### 13.1 Agent Run

- runId、threadId、traceId、userId。
- 原始问题的脱敏摘要。
- intent、status、startedAt、finishedAt。
- modelCalls、toolCalls、tokenUsage、durationMs。
- 最终错误码或答案摘要。

### 13.2 Agent Step

- stepId、runId、agentName、nodeName。
- inputSummary、outputSummary。
- startedAt、finishedAt、durationMs。
- status和errorCode。

### 13.3 Tool Call

- toolCallId、runId、toolName、riskLevel。
- sanitizedArgs、resultSummary。
- durationMs、status、idempotencyKey。

不得记录：API Key、JWT、Service Token、完整文档正文和隐藏推理。

## 14. Agent测试要求

- Router单元测试。
- Requirement结构化抽取测试。
- Tool Schema和错误映射测试。
- LangGraph节点与条件边测试。
- HITL accept/edit/reject/resume测试。
- 异步业务结果重复回调测试。
- OR-Tools硬约束属性测试。
- RAG引用存在性测试。
- 离线评测集端到端测试。
- 会后草案 Schema、负责人白名单、一次修复、Provider 失败和不产生业务写副作用测试。
