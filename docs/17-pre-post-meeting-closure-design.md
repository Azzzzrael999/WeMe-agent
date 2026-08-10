# 会前准备与会后执行闭环设计

## 1. 目标与范围

本切片把原 `/lifecycle` 静态预览替换为真实业务闭环，且不依赖邮件、日历、网盘、视频会议或即时通信平台。

实现范围：

- 会前议程与材料元数据的原子保存。
- 基于当前 Java 事实动态计算准备清单。
- 会前 24 小时、30 分钟提醒，以及 24 小时时缺失项通知。
- 会议结束后的幂等自动完成。
- 发起人提交纯文本会议记录，现有 Requirement Agent 生成纪要、决策和行动项草案。
- 发起人或 ADMIN 对草案执行 `ACCEPT/EDIT/REJECT`；只有 `ACCEPT` 写入正式记录。
- 行动项状态更新、临期提醒和逾期催办。

明确剔除：RSVP、签到、附件二进制上传、云盘链接同步、持久化政策检查、参会者二次确认、多级审批、会议统计与复盘看板。

## 2. 权限与状态

- 会议可见性沿用既有规则：发起人、参会者可读，ADMIN 可读全部。
- 只有发起人或 ADMIN 能编辑会前准备、提交会议记录和审核草案。
- 会前准备只允许在尚未开始的 `CONFIRMED` 会议修改。
- 文本记录和会后草案只允许在 `COMPLETED` 会议创建。
- 行动项负责人或会议发起人、ADMIN 可以更新行动项状态；其他参会者只读。
- `COMPLETED` 是正式会议终态之一；到期扫描只允许 `CONFIRMED -> COMPLETED`，重复扫描幂等。

草案状态：

```text
PROCESSING -> PENDING_REVIEW -> ACCEPTED
                         \-> REJECTED
PROCESSING -> FAILED
```

`EDIT` 保持 `PENDING_REVIEW`，递增草案版本并要求再次确认。`ACCEPTED/REJECTED` 为终态。

行动项状态：

```text
OPEN -> IN_PROGRESS -> DONE
OPEN ----------------> DONE
IN_PROGRESS ---------> OPEN
```

## 3. 数据模型

Flyway V9 新增：

- `meeting_lifecycle_profile(meeting_id, preparation_version, created_at, updated_at)`：会前聚合的乐观版本。
- `meeting_agenda_item(meeting_id, sequence_no, topic, owner_employee_id, planned_minutes)`。
- `meeting_material(meeting_id, sequence_no, title, owner_employee_id, required, status, version_label, note)`；状态仅 `MISSING|READY`，不保存文件内容或外部凭证。
- `meeting_reminder_delivery(meeting_id, meeting_start_at, recipient_id, reminder_type)`；唯一键用于调度重放去重。
- `post_meeting_draft(meeting_id, request_id, agent_run_id, transcript, payload_json, status, version, error_code, reviewed_by, reviewed_at)`；一个会议保留一条当前草案，终态后不可静默覆盖。
- `meeting_minutes(meeting_id, background, discussion_summary, conclusion, confirmed_by, confirmed_at)`。
- `meeting_decision(meeting_id, sequence_no, content, rationale)`。
- `meeting_action_item(meeting_id, sequence_no, title, description, assignee_employee_id, due_at, status, version, completed_at)`。
- `action_item_reminder_delivery(action_item_id, due_at, recipient_id, reminder_type)`；唯一键用于临期/逾期提醒去重。

正式纪要、决策和行动项只在审核接受事务中产生。草案 JSON 不是正式业务记录。

## 4. 准备清单

`GET /api/v1/meetings/{meetingId}/lifecycle` 每次按当前事实计算以下检查项：

| 编码 | 通过条件 |
|---|---|
| `AGENDA_PRESENT` | 至少一个议题 |
| `AGENDA_DURATION` | 议题总时长大于 0 且不超过会议时长 |
| `AGENDA_OWNERS` | 每个议题负责人均为当前会议参与者 |
| `MATERIALS_READY` | 所有 `required=true` 材料状态为 `READY` |
| `ROOM_ACTIVE` | 当前会议室仍为 `ACTIVE` |
| `PARTICIPANTS_PRESENT` | 至少存在一个 REQUIRED 参与者 |

准备状态只有 `READY|NEEDS_ATTENTION`。返回具体失败项和可执行提示，不保存容易过期的聚合结论。

## 5. 定时任务

Java 每分钟执行有界批量扫描：

1. 将 `endAt <= now` 的 `CONFIRMED` 会议条件更新为 `COMPLETED`。
2. 对 `now < startAt <= now + 24h` 的会议向组织者与参会者发送一次 `MEETING_REMINDER_24H`。
3. 在 24 小时扫描中，如动态准备清单未通过，只向组织者发送一次 `PREPARATION_MISSING`，正文列出失败项。
4. 对 `now < startAt <= now + 30m` 的会议发送一次 `MEETING_REMINDER_30M`。
5. 对未完成且 `now < dueAt <= now + 24h` 的行动项向负责人发送一次 `ACTION_ITEM_DUE_SOON`。
6. 对未完成且 `dueAt <= now` 的行动项向负责人发送一次 `ACTION_ITEM_OVERDUE`。

投递日志唯一键以会议开始时间或行动项截止时间作为事实快照；改期或编辑截止时间后可以针对新事实重新提醒，重复扫描不会重复通知。

## 6. 会后 Agent 与 HITL

公共入口只在 Java：

```text
POST /api/v1/meetings/{meetingId}/post-meeting-drafts
POST /api/v1/meetings/{meetingId}/post-meeting-drafts/{draftId}/review
```

创建流程：

1. Java 鉴权并读取会议、房间和参会者快照，先提交 `PROCESSING` 草案；外部 HTTP 不在数据库事务内。
2. Java 使用新的 `traceId/runId` 和短期 AgentContextToken 调用 Python `POST /internal/v1/post-meeting/drafts`。
3. Python 只把已鉴权快照和长度受限的文本记录交给现有 Requirement Agent；Pydantic 校验结构化输出，最多修复一次。
4. Python 校验行动项 `assigneeEmployeeId` 只能来自输入参与者；未知负责人改为 `null`，不得编造 ID。
5. Java 再次执行长度、人数、负责人、截止时间和会议状态校验，然后把草案更新为 `PENDING_REVIEW`。
6. Agent 不可用或输出不合格时草案进入 `FAILED`，正式表保持不变，用户可显式重试。

审核流程：

- `EDIT`：只允许替换结构化草案字段，递增 `version`，保持待审，不写正式表。
- `REJECT`：条件更新草案为 `REJECTED`，不写正式表。
- `ACCEPT`：锁定草案并检查版本、会议状态与权限；一个事务内写入 `meeting_minutes`、`meeting_decision`、`meeting_action_item` 并把草案置为 `ACCEPTED`。

行动项负责人必须是当前会议参与者或组织者；存在行动项时负责人和截止时间必填，截止时间必须晚于会议结束。重复或过期版本返回稳定冲突。

## 7. API 摘要

```text
GET   /api/v1/meetings/{meetingId}/lifecycle
PUT   /api/v1/meetings/{meetingId}/preparation
POST  /api/v1/meetings/{meetingId}/post-meeting-drafts
POST  /api/v1/meetings/{meetingId}/post-meeting-drafts/{draftId}/review
PATCH /api/v1/meetings/{meetingId}/action-items/{actionItemId}

POST  /internal/v1/post-meeting/drafts
```

会前保存请求：

```json
{
  "expectedVersion": 0,
  "agendaItems": [
    {"topic": "确认发布范围", "ownerEmployeeId": 1001, "plannedMinutes": 20}
  ],
  "materials": [
    {
      "title": "上线方案 V3",
      "ownerEmployeeId": 1002,
      "required": true,
      "status": "READY",
      "versionLabel": "v3",
      "note": "已完成评审"
    }
  ]
}
```

会后草案结构：

```json
{
  "minutes": {
    "background": "...",
    "discussionSummary": "...",
    "conclusion": "..."
  },
  "decisions": [{"content": "...", "rationale": "..."}],
  "actionItems": [
    {
      "title": "补充回滚演练",
      "description": "...",
      "assigneeEmployeeId": 1002,
      "dueAt": "2026-08-20T18:00:00+08:00"
    }
  ]
}
```

## 8. 前端

删除 `preview.ts` 中会前会后静态数据和 Product Preview 标识。真实页面按会议选择展示：

- upcoming `CONFIRMED`：议程、材料、准备清单和保存动作。
- `COMPLETED` 且无正式记录：文本记录输入、生成草案、编辑/接受/拒绝。
- 已接受：正式纪要、决策和行动项；行动项按权限更新状态。
- `CANCELLED`：只读提示，不允许准备或会后生成。

页面不展示 RSVP、签到、政策绑定或统计卡片。

## 9. 验收证据

- Java：Flyway V1-V9、权限/状态/乐观锁、动态清单、提醒去重、自动完成、会后审核事务、行动项催办测试。
- Python：Pydantic 输出、负责人白名单、一次修复、fixture 与 Provider 失败测试。
- Frontend：TypeScript 类型检查和生产构建；真实 API 的 READY/缺失、待审和已接受状态。
- 集成：两套 Compose config、应用镜像、公共 API Smoke；不得用静态 preview 数据代替。
