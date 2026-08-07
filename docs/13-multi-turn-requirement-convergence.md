# 多轮需求收敛详细设计

## 1. 目标与边界

本切片解决会议自然语言输入不能一次说全的问题。运行时 Agent 仍为 Supervisor、Requirement、Policy、Scheduling；时间补全、人员范围解析、槽位合并、澄清计划和恢复处理均为确定性组件。浏览器仍只访问 Java，Python 不读取 Java 业务库，预约仍必须经过候选、无占用草案和 HITL。

## 2. 状态模型

关键槽位为 `timeWindow`、`durationMinutes`、`requiredParticipants`。每个槽位记录值、状态、用户可见摘要、来源文本、规则标识和 revision：

- `EXPLICIT`：用户明确说明或从固定起止时间确定性推导。
- `DEFAULTED`：由当前月/周/日及时段规则补全。
- `DIRECTORY_RESOLVED`：Java 根据当前登录用户和组织事实返回。
- `MISSING`：没有足够信息。
- `AMBIGUOUS`：存在多个可信解释。
- `CONFLICT`：新旧明确值或跨字段规则冲突。
- `UNSPECIFIED`：非刚需可选项尚未说明，只提示一次且不阻塞。
- `CLOSED`：用户明确表示没有其他可选要求，或在已有硬性设备后明确结束补充。

部分 `RequirementDraft` 持久化在 Run checkpoint；内部可以生成带安全占位值的 `MeetingRequest` 校验投影，但三个刚需槽位未收敛前不得把它当作可执行需求，也不得进入忙闲、房间或草案 Tool。最新一轮用户明确输入优先级最高，历史明确值其次，通讯录解析再次，系统默认最低。用户没有提到某字段时不得清除历史值。

## 3. 时间规则

所有计算以服务端 `requestTime` 和 `Asia/Shanghai` 为准。日期和时段先分别解析再组合；晚上允许跨午夜。默认结果必须通过日期合法、未来时间、30分钟边界、窗口不短于时长校验。只给明确24小时制时刻但未给时长时，checkpoint 保存 `pendingStartAt`，下一轮补充时长后再确定结束时间，不能丢掉首轮时刻。`最好/尽量/优先`生成 `PREFERRED_START` 软约束；`必须/就/固定`生成硬开始时间。单独“2点”优先继承已有上午/下午上下文，否则作为当天模糊时刻请求用户确认。

## 4. 人员规则

明确姓名继续由 `resolve_employees` 唯一解析。人数只影响容量。“我的小组/同组人员”提取为 `MY_DEPARTMENT`，由确定性节点调用 `resolve_participant_scope`。Java 根据 AgentContext 当前用户查唯一所属部门及 ACTIVE 成员，Python只保存脱敏业务字段。返回单一名单后作为可纠正假设展示，并作为本次会议的 REQUIRED 人员参与忙闲检查。

后续人员修改采用确定性 Delta：`加上/邀请`为 ADD，`去掉/删除/不参加/请假不会来/排除`为 REMOVE，`改成/只有/就这些人`为 REPLACE。REMOVE 只能命中上一版已验证名单中的姓名；ADD 的新姓名仍必须经 `resolve_employees`；任一 Delta 应用后都重新计算容量和忙闲人员，禁止沿用被删除人员的缓存结果。

## 5. 澄清与续接

澄清一次列全，不逐字段串行追问：先展示系统补全和通讯录推定，再列全部阻塞问题，最后提示一次非刚需设备/地点。前端在当前 Run 为 `WAITING_USER_INPUT` 时向 `/agent/runs/{runId}/input` 提交；Java签发同 runId、新 traceId 的 AgentContext，Python在运行锁内加载 checkpoint、校验 revision、把新消息解析成增量并合并。

`/input` 不接受确认 token。现有 `/resume` 不接受普通消息。需求续接成功后可以再次进入 `WAITING_USER_INPUT`，也可以进入 Scheduling 并产生 Top 3 与 `hitl.required`。

Run 进入 `FAILED` 后不能继续累计已耗尽的预算。前端下一次普通输入可以向新建 Run 请求携带 `baseRunId`；Python仅允许继承同用户、同 thread、状态为 `FAILED` 且存在 Requirement checkpoint 的基线。新 Run 从 Requirement 节点开始，复制 Draft、RequirementItem、revision、可选项关闭状态和仍匹配最终名单的已验证人员；模型/Tool计数、候选、草案、确认令牌、写入状态、冲突状态和调用指纹全部归零。没有显式 `baseRunId` 时仍表示一场全新需求。

## 6. 示例验收

固定 `requestTime=2026-08-14T10:00:00+08:00`：

1. 用户：“我要在25号下午安排一场小组会议，给我找个空的会议室”。
2. 系统保存时间 `2026-08-25T12:00:00+08:00` 至 `18:00`，状态 `DEFAULTED`；调用 Java 解析当前部门名单，状态 `DIRECTORY_RESOLVED`；时长为 `MISSING`；返回一次性澄清并进入 `WAITING_USER_INPUT`，不得调用忙闲、房间、草案或写 Tool。
3. 用户：“会开2个小时，要有投屏，没别的要求，最好是2点开始”。
4. 系统合并为时长120分钟、投屏硬约束、14:00软偏好，保留上一轮时间和人员；重新读取忙闲/房间，OR-Tools按14:00偏差排序，生成最多3个候选和草案，进入 `WAITING_CONFIRMATION`。
5. 用户 ACCEPT 前数据库不得创建正式会议或槽位。

## 7. 回归矩阵

- 当前月日期、当前周星期、只说时刻、四类时段、晚上跨午夜、已过去默认值。
- 固定起止推导时长、搜索窗口与时长并存、半小时边界、窗口短于时长。
- 人数不扩写姓名、当前部门范围、无部门、空部门、明确姓名覆盖范围、同名员工。
- `WAITING_USER_INPUT` 归属、revision、重复 `clientRequestId`、并发补充锁和重启后 checkpoint 恢复。
- 非刚需 `UNSPECIFIED/EXPLICIT/CLOSED` 展示；人员 ADD/REMOVE/REPLACE；失败 Run 基线继承及跨用户/跨 thread/非 FAILED 拒绝。
- 两轮 Golden Path、无 DRAFT/WRITE 提前调用、HITL ACCEPT/EDIT/REJECT 回归。
