# 15. 员工管理与站内会议通知详细方案

## 1. 目标与边界

本切片补齐两个现有业务缺口：管理员员工账户管理，以及所有已登录用户的站内消息中心。它不引入新的 RBAC 角色、组织审批、真实邮件、短信、外部日历或视频会议供应商。

- Java 继续是员工、部门、会议与通知的唯一事实源。
- 浏览器只访问 Java `/api/v1/**`；Python Agent 不新增员工管理或通知读写 Tool。
- 员工不物理删除，只允许 `ACTIVE <-> DISABLED`，避免破坏历史会议外键与审计链。
- 站内通知仅允许本人读取和更新读状态；ADMIN 也不能读取其他人的消息。
- 通知写入与会议业务变更处于同一数据库事务。列表读取不依赖 RocketMQ 是否可用。

## 2. 管理员员工管理

### 2.1 能力

ADMIN 可以：

1. 按关键字、部门、角色和状态分页查询员工。
2. 查看员工详情和可选的 ACTIVE 部门列表。
3. 新增员工账户，设置初始密码、部门、角色和状态。
4. 修改展示名、邮箱、部门和角色；用户名创建后不可修改。
5. 启用或停用员工。
6. 重置员工密码；接口只接收明文新密码并立即 BCrypt 哈希，响应、日志和 Trace 均不得回显密码或哈希。

### 2.2 并发与安全规则

- `sys_user` 新增 `version`，编辑、状态变更和密码重置均使用 `expectedVersion` 条件更新，成功后版本加一。
- 用户名和邮箱由数据库唯一约束做最终裁决，并映射为稳定错误码；用户名规范为 3–64 位字母、数字、点、下划线或连字符，存储前转小写。
- 角色固定为 `EMPLOYEE|ADMIN`，状态固定为 `ACTIVE|DISABLED`；部门可为空，非空时必须指向 ACTIVE 部门。
- 当前 ADMIN 不能停用自己，也不能把自己的角色改为 EMPLOYEE，防止当前会话自锁；可以修改自己的展示名、邮箱、部门和密码。
- 已停用用户的既有 JWT 会在下一次请求时被 Java 的活动账户复核拒绝；历史会议和通知保留。
- 密码长度为 8–72 个字符；API 不提供查看密码或导出密码能力。

## 3. 站内消息与会议通知

### 3.1 事件与接收人

通知类型固定为：

| 类型 | 触发时机 | 接收人 |
|---|---|---|
| `MEETING_CONFIRMED` | 手动或 Agent/HOT 预约最终成功 | 组织者和全部参与者去重 |
| `MEETING_CHANGED` | 手动或 Agent 改期/编辑成功 | 修改前与修改后的组织者/参与者并集 |
| `MEETING_CANCELLED` | 手动或 Agent 取消成功 | 组织者和取消时全部参与者去重 |
| `RESOURCE_UNAVAILABLE` | ADMIN 停用房间并为未来已确认会议创建异常单 | 仅会议组织者 |
| `RESOURCE_RESTORED` | 原房间恢复且会议尚未移动，异常单转 RESTORED | 仅会议组织者 |

草案、HITL 等待、HOT `PENDING` 和最终 `CONFLICT` 不生成“会议已确认”通知。重复幂等请求、重复 MQ 消息不得重复生成通知。

### 3.2 用户能力

所有 `EMPLOYEE|ADMIN` 用户可以：

1. 分页查看自己的通知，按未读和类型过滤。
2. 查看未读数量，供全局导航徽标展示。
3. 将一条属于自己的通知标为已读；重复操作返回当前结果。
4. 一次性将自己的全部未读通知标为已读。
5. 从带 `relatedReplanCaseId` 的资源通知进入异常重排单；普通会议通知仍通过 `relatedMeetingId` 进入会议详情，两者都由目标资源可见性规则二次鉴权。

通知列表按 `createdAt DESC, id DESC` 排序。任何查询和更新都必须带 `user_id = 当前登录用户` 条件；不存在或不属于当前用户统一返回 `NOTIFICATION_NOT_FOUND`，避免枚举他人消息。

## 4. 公共 API

员工管理：

```text
GET   /api/v1/admin/departments
GET   /api/v1/admin/employees?keyword=&departmentId=&role=&status=&page=&size=
GET   /api/v1/admin/employees/{employeeId}
POST  /api/v1/admin/employees
PUT   /api/v1/admin/employees/{employeeId}
PATCH /api/v1/admin/employees/{employeeId}/status
POST  /api/v1/admin/employees/{employeeId}/password
```

站内消息：

```text
GET   /api/v1/notifications?unreadOnly=&type=&page=&size=
GET   /api/v1/notifications/unread-count
PATCH /api/v1/notifications/{notificationId}/read
PATCH /api/v1/notifications/read-all
```

全部响应继续使用公共成功/错误信封。详细字段以 `docs/05-data-and-api-spec.md` 为准。

## 5. 前端信息架构

- 左侧导航新增“消息中心”，所有登录角色可见，并显示未读徽标；进入页面或执行已读操作后刷新数量。
- ADMIN 的“管理”分组新增“员工管理”；普通员工不渲染入口，直接访问路由仍由前端守卫和 Java RBAC 双重拒绝。
- 员工页包含搜索/筛选、分页表格与移动卡片、新增/编辑 Sheet、启停确认、密码重置 Dialog；敏感密码字段提交后立即清空。
- 消息页包含全部/未读筛选、类型筛选、单条已读、全部已读、空状态和失败重试；资源通知优先提供“处理异常”入口，普通会议通知提供“查看会议”入口。

## 6. 验证矩阵

### 6.1 Java

- ADMIN 完成员工新增、分页检索、编辑、启停和密码重置；EMPLOYEE 对全部管理员员工接口返回 403。
- 用户名/邮箱冲突、未知部门、非法角色/状态、过期版本、自我停用/降权均返回稳定错误。
- 停用后旧 JWT 在下一请求失效，重置后新密码可登录且旧密码失败。
- 两个用户只能看到并更新自己的通知；单条和全部已读幂等。
- 创建、改期、取消与 HOT 成功生成正确接收人通知；改期删除的旧参与者仍收到变更通知；冲突、回滚和重复消息不产生错误或重复通知。

### 6.2 前端与集成

- TypeScript 类型检查与生产构建通过；EMPLOYEE/ADMIN 路由和导航可见性正确。
- 管理员页面真实完成一名测试员工的新增、编辑、停用、启用和密码重置。
- 员工创建会议后，组织者与参会者消息中心出现通知；标记单条/全部已读后未读徽标同步归零。
- 基础与开发 Compose 配置通过；公共 API Smoke 覆盖 RBAC、员工生命周期、通知隔离和会议通知。
