# 智能会议 Agent 全功能演示验收手册

## 1. 本次演示基线

- 基准日期：2026-08-15，时区 `Asia/Shanghai`。
- 入口：`http://localhost`。
- 员工账号：`zhangsan / demo-password`。
- 管理员账号：`admin / demo-password`。
- 当前真实演示数据：20 间启用会议室、28 位在职员工、22 份会议制度文档。
- 当前可复用会议：
  - `meetingId=121`：架构评审，2026-08-25 13:00–14:00，张三发起，李四必需参加，会议室为研发楼评审室。
  - `meetingId=122`：会议安排，2026-08-25 14:00–16:00，张三发起，李四、赵六必需参加。
  - `meetingId=124`：架构评审，已完成，尚未生成会后草案。
  - `meetingId=132`：支付网关 V2 上线复盘，已完成，已有正式纪要、决策和行动项。

本手册使用 2026-08-26 至 2026-08-31 作为新建会议窗口，避免与现有 8 月 25 日会议混淆。若数据库状态已经变化，保持输入语义不变，把日期整体顺延到未来 7–14 天内的工作日即可。

## 2. 启动与演示前检查

不要删除命名卷，不要覆盖已有 `.env`。完整 HOT 回调演示需要让 Java 的 Agent 结果消费者处于开启状态；本次用进程级变量覆盖，不修改本地 `.env`：

```powershell
Set-Location D:\agent
$env:AGENT_CALLBACK_ENABLED = 'true'
docker compose config --quiet
docker compose up -d --build --wait
docker compose ps
Remove-Item Env:AGENT_CALLBACK_ENABLED
```

验收启动结果：

- 8 个常驻服务均为 `healthy`：frontend、business-service、agent-service、mysql、redis、qdrant、rocketmq-namesrv、rocketmq-broker。
- `rag-init`、`rocketmq-store-init`、`rocketmq-topic-init` 为 `Exited (0)`，这是正常的一次性任务终态。
- 浏览器打开 `http://localhost` 可见 MeetOps 登录页。
- Agent 使用真实 DeepSeek；RAG 使用本地 BGE-M3、1024 维向量集合 `meeting_policies_bge_m3_v1`。

推荐打开三个观察窗口：

```powershell
docker compose logs -f business-service agent-service
docker compose logs -f rocketmq-broker
docker compose ps
```

## 3. 推荐演示顺序

完整演示约 75–100 分钟。若只有 25 分钟，优先执行 S01、S03、S05、S07、S09、S11、S14 和 S16。

| 顺序 | 场景 | 主要证明点 | 建议时长 |
|---|---|---|---:|
| S01 | 登录、角色和产品工作台 | JWT、RBAC、产品化前端 | 3 分钟 |
| S02 | 会议室与真实可用性 | 20 间资源、30 分钟槽位、中文设备 | 4 分钟 |
| S03 | RAG 有据与无据问答 | BGE-M3、Qdrant、Policy Agent、引用忠实 | 6 分钟 |
| S04 | 只读共同时间与会议室推荐 | Tool Calling、无写入副作用 | 5 分钟 |
| S05 | 多轮需求收敛与 Top 3 | Supervisor + Requirement + Scheduling、OR-Tools | 8 分钟 |
| S06 | HITL EDIT / REJECT / ACCEPT | 旧 token 作废、再校验、零提前写入 | 8 分钟 |
| S07 | Agent 改期和取消 | Before/After、目标会议唯一识别、三类草案 | 7 分钟 |
| S08 | 歧义、组织范围与无解恢复 | 澄清、组织 Tool、结构化 UNSAT | 8 分钟 |
| S09 | 手动预约闭环 | Agent 降级边界、Java 事实源 | 5 分钟 |
| S10 | 并发与幂等 | Redis 预占、MySQL 最终裁决 | 5 分钟 |
| S11 | HOT + Outbox + RocketMQ + 恢复 | PENDING、至少一次投递、checkpoint、重规划 | 8 分钟 |
| S12 | 消息中心 | 事务通知、接收人隔离、已读幂等 | 3 分钟 |
| S13 | 员工与会议室管理 | ADMIN RBAC、乐观锁、资源状态 | 6 分钟 |
| S14 | 资源失效与异常重排 | 异常单、Top 3、双版本、禁止自动移动 | 7 分钟 |
| S15 | 会前准备 | 议程、材料、动态清单、版本冲突 | 5 分钟 |
| S16 | 会后草案与行动项 | Requirement 复用、HITL、事务、催办 | 7 分钟 |
| S17 | 知识库管理 | 上传、编辑、删除、tombstone、重新索引 | 7 分钟 |
| S18 | 自动化证据收尾 | Smoke、并发、评测报告 | 5 分钟 |

## 4. 浏览器逐步演示

### S01 登录、角色与工作台

1. 用 `zhangsan` 登录。
2. 观察侧栏：智能编排、我的会议、会议室、消息中心、知识库、异常重排、会前会后可见；员工管理不可见。
3. 手动访问 `http://localhost/admin/employees`，应被路由回智能编排。
4. 退出后用 `admin` 登录，员工管理入口出现，会议室和知识库页面出现管理按钮。

验收证据：前端入口隐藏与 Java 403 双重生效；响应和页面不展示密码、哈希、JWT 或内部 Service Token。

### S02 会议室与 30 分钟可用性

1. 用张三打开“会议室”。
2. 依次查看研发楼评审室、总部楼 VIP 501、创新楼共创工作坊。
3. 日期选 `2026-08-26`，查看 08:00–00:00 的半小时槽位。
4. 切换容量、楼栋和设备筛选，确认页面自动重新查询。

验收证据：会议室只显示中文产品信息；已查询槽位区分空闲/占用，尚未返回的槽位显示“待查询”；旧日期请求不能覆盖当前日期。

### S03 RAG 制度问答：有依据与无依据

用张三进入“智能编排”，先输入：

```text
接待重要客户能直接用 VIP 会议室吗？请只按公司制度回答，并告诉我依据。
```

预期：Supervisor 路由到 Policy Agent；返回“VIP 与高管会议室使用规则”的真实引用，引用可展开到标题、章节和 chunk；不进入 Scheduling，不出现候选或 HITL。

再输入：

```text
公司是不是规定每月最后一个周五开会必须穿蓝色衣服？没有制度依据就直接说没查到。
```

预期：明确返回“未找到可验证的会议制度证据”，引用数量为 0，不补充泛化常识。

验收证据：Trace 中有 Supervisor、Policy 和只读检索步骤；没有建草案或确认写 Tool。

### S04 只读共同时间与会议室推荐

输入共同时间查询：

```text
帮我看看李四和赵六在 2026 年 8 月 26 日上午 9 点到 12 点有没有一起空出的 60 分钟，只查时间，不预约。
```

预期：意图为 `FIND_COMMON_TIME`，调用员工解析和忙闲 Tool，返回可行时段；直接完成，无草案、无确认按钮。

输入会议室推荐：

```text
2026 年 8 月 26 日 13:00 到 17:00，8 个人，给我找带大屏和白板的会议室，只推荐，不要预约。
```

预期：意图为 `RECOMMEND_ROOM`，只查会议室和占用，返回推荐但不创建草案。

### S05 多轮需求收敛、原生 Tool Calling 与 OR-Tools Top 3

新建对话，第一轮输入：

```text
帮我安排一个演示验收评审。
```

预期：一次性展示已确认事实和全部阻塞项，至少询问日期/时间窗口、时长和必需参会者；状态为等待补充，不查房、不生成草案。

第二轮输入：

```text
2026 年 8 月 27 日 13:00 到 17:00 之间，开 60 分钟；李四、赵六必须参加，8 个人，需要大屏和白板，其他没有要求。
```

预期链路：

1. 同一 Run 的需求 revision 递增，不丢失“演示验收评审”。
2. Requirement Agent 解析时间、时长、两名 REQUIRED、容量和两项设备。
3. Java Tool 解析员工、查询忙闲和会议室；模型给出的人员 ID 不可信。
4. Scheduling Agent 调用 OR-Tools，独立验证器复核硬约束，返回最多 3 个按成本升序的候选。
5. 创建无占用 CREATE 草案并暂停在 HITL；会议列表中暂时不存在该会议。

打开 Trace，指出 `agent.loop` 的 PLAN / ACT / OBSERVE / VERIFY、Tool 摘要、预算、候选成本和无隐藏推理边界。

### S06 HITL EDIT、REJECT、ACCEPT

在 S05 的草案上执行：

1. 选择第二候选或把开始时间改成同一窗口内另一个 30 分钟边界，点击 `EDIT`。
2. 验证系统重新查询、求解和校验，返回新草案；旧确认 token 作废，仍未创建正式会议。
3. 先用另一条相同请求演示 `REJECT`，确认 Run 结束且会议列表无新增。
4. 回到当前合法草案执行 `ACCEPT`。
5. 验证出现 `booking.completed`，只创建一场 `CONFIRMED` 会议；“我的会议”和消息中心可见。

验收证据：只有 ACCEPT 后 Trace 才出现 `confirm_booking` WRITE Tool；重复点击或重放旧 token 不会重复创建。

### S07 改期与取消三类 HITL

对 S06 已创建的会议输入：

```text
把刚才创建的“演示验收评审”改到 2026 年 8 月 28 日上午 10 点，时长、人员和设备都不变，先给我看变更草案。
```

预期：意图为 `MODIFY_MEETING/RESCHEDULE`；只在当前用户可管理会议内唯一定位；展示 Before/After；查询忙闲和房间时只排除目标会议自身；ACCEPT 前原会议不变。接受后验证会议版本递增、人员和时长保持。

接着输入：

```text
取消刚才改期的“演示验收评审”，先让我看清楚会取消哪一场，不要直接动。
```

第一次执行 `REJECT`，确认会议仍为 `CONFIRMED`。再次输入并执行 `ACCEPT`，确认会议为 `CANCELLED`、槽位释放并产生取消通知。

### S08 歧义、我的小组和结构化无解恢复

歧义时间输入：

```text
2026 年 8 月 28 日 2 点帮我和李四开一小时会。
```

预期：询问上午/下午，不擅自采用 14:00。继续输入：

```text
是下午两点，4 个人，不需要额外设备。
```

预期：同一 Run 形成 14:00 的候选；演示后 REJECT。

组织范围输入：

```text
给我的小组约个 2026 年 8 月 31 日下午的 60 分钟周会，至少 12 人，要白板，房间你挑，先给方案。
```

预期：调用 `resolve-participant-scope`，由 Java 根据张三所属部门返回 ACTIVE 成员；页面展示可纠正人员范围，模型不能凭空编名字。演示后 REJECT。

无解第一轮输入：

```text
我必须参加，请在 2026 年 8 月 25 日 13:00 到 16:00 之间安排 60 分钟会议，4 个人，不需要设备；如果排不开请说具体原因。
```

张三在该窗口已有两场连续会议，预期返回结构化 `plan.unsat`：请求窗口、时长、无解类别、具体冲突时段/会议和有限建议，不伪造候选。

继续输入：

```text
那改到 2026 年 8 月 26 日上午 9 点到 12 点这个范围，时长和其他要求不变。
```

预期：仍是尚未落库的 CREATE 需求修订，不误判为修改正式会议；重新求解并进入 HITL。演示后 REJECT。

### S09 Java 手动预约、更新与取消

进入“我的会议”，创建：

| 字段 | 输入 |
|---|---|
| 标题 | 手动预约降级演示 |
| 类型 | 架构评审 |
| 日期/时间 | 2026-08-26 15:00–16:30 |
| 会议室 | 选择页面显示为空闲且有大屏/白板的房间 |
| 必需参会者 | 张三、李四 |
| 可选参会者 | 王五 |

预期：90 分钟占用 3 个连续半小时槽位；直接提交即是人工确认，不绕行 Agent，但复用 Java 同一事务/校验服务。

随后把标题改为“手动预约降级演示（已更新）”并换到另一个可用房间；最后取消。确认创建、变更、取消通知和槽位释放。

可选降级证明：临时停止 `agent-service`，刷新后手动 CRUD 仍可用，Java readiness 不因 Agent 不可用而失败；完成后重新启动 Agent：

```powershell
docker compose stop agent-service
docker compose start agent-service
docker compose wait agent-service
```

### S10 100 并发和幂等

这些脚本随机选择未来 1–13 天的空闲窗口，成功会议会通过公共取消 API 清理：

```powershell
python scripts/concurrency-day2.py --mode room --requests 100 --workers 32
python scripts/concurrency-day2.py --mode idempotency --requests 100 --workers 32
```

预期：

- room：1 success、99 conflict、1 unique meeting。
- idempotency：100 success、0 conflict、1 unique meeting。
- 报告包含 P50/P95/P99；最终依据是数据库唯一业务结果，不只是 HTTP 返回。

### S11 HOT、Outbox、RocketMQ 和 checkpoint 恢复

最稳定的完整演示使用公共 API Smoke；它会创建竞争会议、触发 HOT PENDING、等待 MQ CONFLICT 回调、从 checkpoint 重规划、再次确认成功，并用正常取消接口清理测试会议：

```powershell
python scripts/smoke-day5.py --public-trace
```

演示时同步观察日志，指出：

1. 草案确认后先返回 `PENDING + requestNo`，正式 meeting 尚不存在。
2. 同一事务写 booking_request 和 BOOKING_COMMAND Outbox。
3. RocketMQ 按至少一次投递；消费者以 `eventId + 业务终态` 幂等，不宣称基础设施 exactly-once。
4. 冲突后 BOOKING_RESULT 回调恢复原 Run，排除失败候选并重新求解。
5. 新候选必须再次 HITL；进程/页面刷新不会丢失 checkpoint。

### S12 消息中心

用张三打开“消息中心”：

1. 查看 S06/S07/S09 产生的确认、变更和取消通知。
2. 单条标记已读，再执行全部已读，观察侧栏未读数同步变化。
3. 切换管理员，确认管理员也不能读取张三的个人通知。
4. 指出 HOT PENDING/CONFLICT、重复幂等请求和重复 MQ 消息不会伪造成功通知。

### S13 管理员员工与会议室管理

用管理员创建一个临时员工：

| 字段 | 输入 |
|---|---|
| 用户名 | `demo.acceptance.0815` |
| 初始密码 | `Demo-Accept-2026!` |
| 姓名 | 演示验收员 |
| 邮箱 | `demo.acceptance.0815@example.test` |
| 部门 | 研发中心 |
| 角色 | EMPLOYEE |
| 状态 | ACTIVE |

依次演示检索、编辑姓名、重置密码、停用、旧 token 下一请求失效、重新启用。重复使用旧版本编辑应得到乐观锁冲突。若用户名已存在，把后缀改为当前日期时间。

会议室管理可新建临时资源：

| 字段 | 输入 |
|---|---|
| 编码 | `DEMO-ACCEPT-815` |
| 名称 | 演示验收室 |
| 楼栋/楼层 | 创新楼 / 8F |
| 容量 | 10 |
| 类型 | 标准会议室 |
| 设备 | 大屏、白板 |
| 热门 | 否 |

验收后可停用临时资源；不要物理删除业务历史。

### S14 会议室失效与异常重排

先完成 S15 对 `meetingId=121` 的会前准备，再执行本场景。

1. 管理员在会议室页停用“研发楼评审室” (`roomId=111`)，原因输入：

```text
演示验收：投影设备临时检修
```

2. 系统应在同一 Java 事务内为会议 121 创建唯一 OPEN 异常单，只通知发起人张三，不自动移动会议。
3. 张三打开“异常重排”，查看原计划、失效原因、保留约束和最多 3 个同时间候选。
4. 使用页面当前候选确认快速换房；服务端用会议版本 + 异常单版本双重裁决，成功后会议房间变化、全员收到变更通知、异常单转 `RESOLVED`。
5. 另一个演示分支可点击“在智能编排中处理”：页面只预填 RESCHEDULE 事实，不自动发送；发送后仍需 HITL。
6. 管理员最后重新启用研发楼评审室。已 RESOLVED 的异常单保持审计终态，不删除。

### S15 会前议程、材料和动态准备清单

用张三打开“会前会后”，选择 `meetingId=121` 的“架构评审”。录入：

议程：

| 顺序 | 议题 | 负责人 | 分钟 |
|---:|---|---|---:|
| 1 | 确认评审目标 | 张三 | 15 |
| 2 | 方案对比与风险 | 李四 | 30 |
| 3 | 行动项确认 | 张三 | 15 |

材料：

| 标题 | 负责人 | 必需 | 初始状态 | 版本 |
|---|---|---|---|---|
| 架构方案 V3 | 李四 | 是 | READY | v3 |
| 压测报告 | 张三 | 是 | MISSING | draft |

先保存并观察清单为 `NEEDS_ATTENTION`；再把压测报告改为 `READY / v1.0`，保存后清单更新。用旧页面/旧版本再次提交应冲突，不能覆盖最新内容。参与者李四只能查看，不能修改。

### S16 会后草案、EDIT/ACCEPT 和行动项

先查看 `meetingId=132`“支付网关 V2 上线复盘”，展示已经 ACCEPT 的正式纪要、1 条决策和 1 个行动项，证明数据不是静态 Preview。

然后选择 `meetingId=124` 的已完成“架构评审”，提交以下虚构文本记录：

```text
本次会议确认支付网关发布范围保持不变。李四负责在 2026 年 8 月 20 日 18:00 前补充回滚演练记录，王五负责核对监控告警阈值。会议决定先完成灰度验证，再进入全量发布。提到的外部顾问“陈老师”不在参会人员白名单内，不应成为系统负责人。
```

预期：复用 Requirement Agent 生成纪要、决策和行动项草案；陈老师被归一为空负责人或需人工修正，不能伪造成业务员工 ID。

1. 先执行 `EDIT`，修正纪要和负责人；正式表仍为空且草案版本递增。
2. 再执行 `ACCEPT`，纪要、决策和行动项在同一事务出现。
3. 用负责人账号更新行动项 `OPEN -> IN_PROGRESS -> DONE`；旧版本更新冲突。
4. 指出临期/逾期扫描按截止时间事实快照去重，DONE 后不再催办。

### S17 知识库浏览、上传、编辑、删除与 tombstone

用管理员新建 UTF-8 Markdown 文件并上传：

```markdown
---
documentId: doc_demo_bluewhale_acceptance_20260815
title: 蓝鲸验收会议临时规则
documentType: MEETING_POLICY
department: 研发中心
version: 1.0
effectiveDate: 2026-08-15
status: ACTIVE
priority: 999
timezone: Asia/Shanghai
---

# 蓝鲸验收会议临时规则

## 适用范围

本规则只用于 MeetOps 演示验收。

## 规则正文

蓝鲸验收会议的组织者必须在会议开始前 24 小时完成材料清单。

## 例外与冲突处理

没有例外；与专项制度冲突时以专项制度为准。

## 常见问题

材料未完成时由组织者补齐，不自动移动其他会议。

## RAG 测试问题

蓝鲸验收会议需要提前多久完成材料清单？
```

验收动作：

1. EMPLOYEE 可查看但没有上传/编辑/删除按钮，直接管理请求为 403。
2. ADMIN 上传后状态为 INDEXED，正文可浏览，Qdrant 使用 BGE-M3 重建 chunks。
3. Agent 提问“蓝鲸验收会议需要提前多久完成材料清单？”，引用应来自新文档。
4. 在线编辑“24 小时”为“48 小时”，使用当前 `recordVersion` 保存；旧版本编辑应冲突。
5. 删除文档后检索不可见，保留 `DELETED` tombstone；重新运行 `rag-init` 不会静默恢复。

不要编辑或删除 22 份正式 seed 制度。

### S18 Trace、评测报告和自动化收尾

最后展示一个成功 Run 的 Trace：

- Run 状态、provider/model、Prompt/Schema 版本、延迟和 Token。
- Supervisor、Requirement、Policy、Scheduling 的结构化步骤。
- Java READ/WRITE Tool 的脱敏参数、风险等级、耗时与结果摘要。
- `agent.loop` 的 phase/iteration/decision/反馈/剩余预算。
- OR-Tools 候选和 RAG 引用。
- 不包含隐藏推理、JWT、确认令牌、Service Token 或完整敏感正文。

展示已提交报告：

- `artifacts/product-scenario-evaluation.json`：16/16 多轮公开 API 对抗场景。
- `artifacts/live-eval/component-full-final-20260815.json`：真实模型 40 条组件门禁。
- `artifacts/fixture-evaluation.json`：网络调用为 0 的确定性组件基线，不称为 E2E。

## 5. 一键自动验收命令

以下 Smoke 都只走 Java 公共入口；脚本使用虚构数据并尽量通过业务取消/删除接口清理，不删除命名卷：

```powershell
Set-Location D:\agent

python scripts/smoke-day6.py --public-base http://localhost
python scripts/smoke-day5.py --public-trace
python scripts/smoke-employee-notifications.py --public-base http://localhost
python scripts/smoke-exception-replan.py --public-base http://localhost
python scripts/smoke-pre-post-meeting.py --public-base http://localhost
python scripts/smoke-rag-document-management.py --public-base http://localhost

python scripts/concurrency-day2.py --mode room --requests 100 --workers 32
python scripts/concurrency-day2.py --mode idempotency --requests 100 --workers 32
```

真实模型公开 API 对抗集：

```powershell
python scripts/evaluate-product-scenarios.py `
  --public-base http://localhost `
  --output artifacts/product-scenario-evaluation-demo.json
```

完整 16 条会调用真实 DeepSeek，耗时和费用取决于供应商；先做快速验收时可只跑：

```powershell
python scripts/evaluate-product-scenarios.py `
  --public-base http://localhost `
  --case create-missing-then-complete `
  --case policy-vip-grounded `
  --case policy-unknown-honesty `
  --case unsat-then-relax-same-run `
  --output artifacts/product-scenario-demo-targeted.json
```

## 6. 最终验收清单

- [ ] Compose 配置通过，8 个常驻服务 healthy，一次性初始化任务 Exited (0)。
- [ ] 手动会议在 Agent 不可用时仍能创建、修改和取消。
- [ ] Supervisor + Requirement/Policy/Scheduling 真实参与各自路径。
- [ ] 原生 Tool Calling 参数经过 Schema、上下文、权限和幂等校验。
- [ ] OR-Tools 返回 Top 3，独立验证器硬约束违规为 0。
- [ ] Policy 回答有可验证引用；无依据问题诚实返回无证据。
- [ ] CREATE/RESCHEDULE/CANCEL 都在 HITL ACCEPT 前零正式写入。
- [ ] EDIT 作废旧 token 并重新读取事实/求解；REJECT 无副作用。
- [ ] 100 路并发同房同槽最多一个成功；同键幂等只产生一个业务结果。
- [ ] HOT 请求经历 PENDING、Outbox、RocketMQ 终态和 checkpoint 恢复。
- [ ] Trace 展示结构化步骤但不泄露隐藏推理和令牌。
- [ ] EMPLOYEE/ADMIN 权限、乐观版本和通知隔离生效。
- [ ] 房间失效只建异常单和通知，不自动移动会议；处理后状态正确。
- [ ] 会前议程/材料、动态清单、会后草案/HITL/行动项为真实闭环。
- [ ] 知识库上传、编辑、删除、BGE-M3 重建和 tombstone 生效。
- [ ] 不把外部日历、邮件、视频会议链接、IoT、SSO、RSVP、附件、统计或自动移动他人会议描述为已实现。

## 7. 演示结束后的状态处理

- 取消所有标题含“演示验收”的临时未来会议；不要直接删数据库记录。
- 重新启用为演示停用的正式会议室。
- 删除临时“蓝鲸验收”知识文档，保留 tombstone 作为审计证据。
- 临时员工可停用，但不物理删除。
- 保留已完成 Run、Outbox、MQ、异常单和通知的审计历史。
- 不执行 `docker compose down -v`，不删除任何命名卷。
