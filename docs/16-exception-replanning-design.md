# 资源失效与异常重排设计

## 1. 目标与边界

本切片覆盖“管理员停用会议室 → 识别受影响会议 → 通知发起人 → 异常页快速换房或智能编排详细重排 → 显式确认 → 一致性关闭异常单”的完整闭环。

- Java 继续是会议、会议室、异常单、通知和最终写入的事实源。
- 停用会议室只创建异常单和通知，不自动移动会议，不通知全部参会者；真正改期或取消后沿用既有全员会议通知。
- 异常页只做原时段固定、时长固定、人员固定、设备能力不降低的“快速换房”。这是一组 Java 只读硬约束过滤结果，不是第二套通用求解器。
- 时间、参会人、地点或设备约束需要变化时，进入智能编排；Python 仍使用 Supervisor + Requirement/Policy/Scheduling、OR-Tools Top 3、独立验证和 RESCHEDULE HITL。
- 不接入真实邮件、短信、外部日历、IoT 或自动故障探测；本期资源失效的受控入口是 ADMIN 将会议室改为 `INACTIVE` 并填写原因。

## 2. 场景

### 2.1 会议室失效

管理员将“研发楼 301”停用并填写“空调漏水，预计今日不可用”。Java 在房间状态版本更新成功的同一事务中，扫描该房间内尚未开始的 `CONFIRMED` 会议；每场会议按“房间停用后的版本 + meetingId”创建唯一异常单，并向发起人创建 `RESOURCE_UNAVAILABLE` 站内通知。重复请求、旧版本请求或同一停用事件重放不能重复建单或重复通知。

### 2.2 异常页快速换房

发起人从消息中心进入异常重排页，看到原计划、失效原因、发生时间、约束快照和当前状态。系统只列出同一 `[start,end)` 内：状态为 `ACTIVE`、容量足够、包含原房间设备能力、且没有房间槽位冲突的最多 3 个候选。用户选择候选并提交后，Java 复用既有会议修改校验与事务服务；会议版本和异常单版本任一过期均返回稳定冲突，页面刷新事实后再决策。

### 2.3 智能编排详细重排

当同一时段没有合适房间，或用户希望改变时间、地点、设备/参与人约束时，异常页生成一条可编辑但不自动发送的对话开场白：

> 请处理异常重排单 RP-20260814-0001。会议 ID 127 的原会议室“研发楼 301”已失效，原因是“空调漏水”。请先读取我可管理的会议事实；默认保留原会议时长、必需/可选参会人和设备要求，优先保持原时段，给出 Top 3，并在任何写入前让我确认。

智能编排的标准对话：

1. Supervisor 将“异常重排/资源失效/会议室不可用”归为 `RESCHEDULE`，不得误归为 CREATE。
2. Requirement 从 Java 可管理会议事实唯一定位显式 meetingId，原时间、时长、人员与设备标为 `INHERITED`；失效房间只能作为排除项，不能成为新候选。
3. Scheduling 先尝试原时段；无解时返回结构化阻塞证据和放宽建议，不自行放宽硬约束。
4. 用户可继续说“允许顺延 30 分钟”“不再要求白板”“只要同一栋楼”等。每次变化显示来源、改变项、保留项与放宽原因，并重新查询事实、求解和验证。
5. 选中方案后生成 RESCHEDULE Before/After 草案。只有 `ACCEPT` 才调用确认 Tool；`EDIT` 作废旧 token 并重新验证；`REJECT` 不改会议且异常单继续为 `OPEN`。
6. Java 确认改期成功的事务同时把异常单标为 `RESOLVED`，随后正常发送 `MEETING_CHANGED` 给修改前后人员并集。

### 2.4 资源恢复、会议取消与竞争

- 房间恢复为 `ACTIVE` 时，仍引用该房间的开放异常单标记为 `RESTORED`，并向发起人发送 `RESOURCE_RESTORED`；已改到其他房间的单据保持 `RESOLVED`。
- 受影响会议被取消时，开放异常单标记为 `CANCELLED`；既有 `MEETING_CANCELLED` 通知承担参会者告知。
- 若管理员、异常页和 Agent 并发操作，MySQL 会议槽位唯一约束仍是最终裁决。异常页额外使用 `expectedMeetingVersion + expectedCaseVersion`，冲突不覆盖最新事实。

## 3. 数据与状态

新增 Java 表 `meeting_replan_case`：

| 字段 | 说明 |
|---|---|
| `id/case_no` | 主键与用户可见单号 |
| `meeting_id/organizer_id` | 受影响会议与发起人 |
| `failed_room_id/failed_room_name` | 失效资源与名称快照 |
| `failure_reason/room_status_version` | 失效原因与停用事件版本 |
| `original_start_at/original_end_at` | 发现异常时的计划时间 |
| `constraint_snapshot` | 标题、人数、人员和原房间设备等结构化快照 |
| `status` | `OPEN/RESOLVED/RESTORED/CANCELLED` |
| `resolution_type` | `QUICK_ROOM_CHANGE/AGENT_RESCHEDULE/MEETING_CANCELLED/RESOURCE_RESTORED` |
| `resolved_room_id/resolved_start_at/resolved_end_at` | 处置后的计划快照 |
| `version/created_at/updated_at/resolved_at` | 乐观锁与审计时间 |

`(meeting_id, failed_room_id, room_status_version)` 唯一，保证一次房间停用事件对一场会议只产生一张单。`notification` 增加可空 `related_replan_case_id`，普通会议通知保持为空。

## 4. 公共 API

- `GET /api/v1/replan-cases?status=OPEN&page=1&size=20`：EMPLOYEE 仅看本人发起的会议，ADMIN 可看全部。
- `GET /api/v1/replan-cases/{caseId}`：返回异常、当前会议、约束变化、保留项和版本。
- `GET /api/v1/replan-cases/{caseId}/alternatives?limit=3`：返回原时段内通过硬约束过滤的替代房间；结果包含排序原因和明确的 `changedConstraints/preservedConstraints`。
- `POST /api/v1/replan-cases/{caseId}/resolve`：请求包含 `roomId`、`expectedMeetingVersion` 和 `expectedCaseVersion`；服务端保留当前会议其余字段并调用同一会议修改服务。
- `PATCH /api/v1/admin/rooms/{roomId}/status`：停用时 `reason` 必填，启用时可省略。

稳定错误码：`REPLAN_CASE_NOT_FOUND`、`REPLAN_CASE_STATE_CONFLICT`、`REPLAN_CANDIDATE_STALE`；既有 `BOOKING_CONFLICT/ROOM_NOT_FOUND/MEETING_STATE_CONFLICT` 继续复用。

## 5. 前端交互

- 导航中的“异常重排”从产品预览升级为真实 `/replan` 页面，显示开放数量、筛选、异常原因、原计划、约束快照和状态。
- 快速换房候选必须显示“仅会议室改变”，以及时间、时长、人员、设备均保持的证据；提交前显示二次确认。
- “在智能编排中详细处理”只预填对话，不自动发送；用户可以检查/补充允许变化的约束后再发送。
- `RESOURCE_UNAVAILABLE/RESOURCE_RESTORED` 通知优先深链到异常单；普通会议通知继续深链到会议详情。
- 页面刷新后从 Java 重新读取状态；已解决/恢复/取消的单据只读展示，不再允许提交旧候选。

## 6. 验收

1. 房间停用、异常单和发起人通知同事务成功；版本冲突或事务回滚时三者均不部分生效。
2. 同一停用事件重放不重复建单/通知；房间恢复再二次停用使用新版本形成新事件。
3. 非发起人不能读取或处理异常单；ADMIN 可运维查看和代处理，但所有最终会议写入仍经过权限和业务校验。
4. 快速候选不包含失效、容量不足、设备缺失或槽位冲突房间；提交后会议、槽位、通知和异常单终态一致。
5. Agent 对异常重排开场归类为 RESCHEDULE，继承事实、排除失效房间、展示 Top 3/无解证据，并在 ACCEPT 前零正式写入。
6. REJECT 后异常单仍 `OPEN`；Agent 或手动改期成功后 `RESOLVED`；取消后 `CANCELLED`；原资源恢复后 `RESTORED`。
