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
  IDEMPOTENCY_KEY_REUSED(HttpStatus.CONFLICT, "幂等键已用于不同的请求"),
  MEETING_NOT_FOUND(HttpStatus.NOT_FOUND, "会议不存在或当前用户不可查看"),
  BOOKING_REQUEST_NOT_FOUND(HttpStatus.NOT_FOUND, "预约请求不存在或当前用户不可查看"),
  MEETING_STATE_CONFLICT(HttpStatus.CONFLICT, "会议状态或版本不允许当前操作"),
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
