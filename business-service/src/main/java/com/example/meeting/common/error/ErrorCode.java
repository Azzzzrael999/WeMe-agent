package com.example.meeting.common.error;

import org.springframework.http.HttpStatus;

public enum ErrorCode {
  AUTH_REQUIRED(HttpStatus.UNAUTHORIZED, "需要登录或登录状态已失效"),
  SERVICE_TOKEN_INVALID(HttpStatus.UNAUTHORIZED, "内部服务令牌无效"),
  AGENT_CONTEXT_INVALID(HttpStatus.UNAUTHORIZED, "Agent 上下文无效"),
  FORBIDDEN(HttpStatus.FORBIDDEN, "没有执行该操作的权限"),
  VALIDATION_ERROR(HttpStatus.BAD_REQUEST, "请求参数不符合要求"),
  BOOKING_CONFLICT(HttpStatus.CONFLICT, "会议室或必须参加者在该时段已被占用"),
  ROOM_NOT_FOUND(HttpStatus.NOT_FOUND, "会议室不存在或当前用户不可查看"),
  ROOM_CODE_CONFLICT(HttpStatus.CONFLICT, "会议室编码已存在"),
  ROOM_STATE_CONFLICT(HttpStatus.CONFLICT, "会议室状态或版本不允许当前操作"),
  EMPLOYEE_NOT_FOUND(HttpStatus.NOT_FOUND, "员工不存在"),
  EMPLOYEE_USERNAME_CONFLICT(HttpStatus.CONFLICT, "员工用户名已存在"),
  EMPLOYEE_EMAIL_CONFLICT(HttpStatus.CONFLICT, "员工邮箱已存在"),
  EMPLOYEE_STATE_CONFLICT(HttpStatus.CONFLICT, "员工状态或版本不允许当前操作"),
  DEPARTMENT_NOT_FOUND(HttpStatus.NOT_FOUND, "部门不存在或已停用"),
  NOTIFICATION_NOT_FOUND(HttpStatus.NOT_FOUND, "通知不存在"),
  REPLAN_CASE_NOT_FOUND(HttpStatus.NOT_FOUND, "异常重排单不存在或当前用户不可查看"),
  REPLAN_CASE_STATE_CONFLICT(HttpStatus.CONFLICT, "异常重排单状态或版本已变化，请刷新后重试"),
  REPLAN_CANDIDATE_STALE(HttpStatus.CONFLICT, "替代会议室已不再满足约束，请刷新候选"),
  IDEMPOTENCY_KEY_REUSED(HttpStatus.CONFLICT, "幂等键已用于不同的请求"),
  MEETING_NOT_FOUND(HttpStatus.NOT_FOUND, "会议不存在或当前用户不可查看"),
  BOOKING_REQUEST_NOT_FOUND(HttpStatus.NOT_FOUND, "预约请求不存在或当前用户不可查看"),
  MEETING_STATE_CONFLICT(HttpStatus.CONFLICT, "会议状态或版本不允许当前操作"),
  MEETING_CONTENT_STATE_CONFLICT(HttpStatus.CONFLICT, "会前内容版本或会议状态不允许当前操作"),
  POST_MEETING_DRAFT_STATE_CONFLICT(HttpStatus.CONFLICT, "会后草案版本或审核状态不允许当前操作"),
  ACTION_ITEM_NOT_FOUND(HttpStatus.NOT_FOUND, "行动项不存在或当前用户不可查看"),
  ACTION_ITEM_STATE_CONFLICT(HttpStatus.CONFLICT, "行动项状态、版本或权限不允许当前操作"),
  DRAFT_EXPIRED(HttpStatus.CONFLICT, "草案已过期"),
  DRAFT_ALREADY_USED(HttpStatus.CONFLICT, "草案已被处理"),
  TOOL_NOT_ALLOWED(HttpStatus.FORBIDDEN, "Agent 工具不允许执行"),
  AGENT_RUN_STATE_CONFLICT(HttpStatus.CONFLICT, "Agent 任务状态已变化，请刷新后重试"),
  AGENT_UNAVAILABLE(HttpStatus.SERVICE_UNAVAILABLE, "Agent 服务暂时不可用"),
  DEPENDENCY_UNAVAILABLE(HttpStatus.SERVICE_UNAVAILABLE, "依赖服务暂时不可用"),
  INTERNAL_ERROR(HttpStatus.INTERNAL_SERVER_ERROR, "系统暂时不可用");

  private final HttpStatus status;
  private final String defaultMessage;

  ErrorCode(HttpStatus status, String defaultMessage) {
    this.status = status;
    this.defaultMessage = defaultMessage;
  }

  public HttpStatus status() {
    return status;
  }

  public String defaultMessage() {
    return defaultMessage;
  }
}
