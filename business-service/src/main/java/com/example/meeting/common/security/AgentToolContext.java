package com.example.meeting.common.security;

import java.util.List;

public record AgentToolContext(
    long userId,
    String username,
    List<String> roles,
    String traceId,
    String runId,
    String toolCallId) {

  public static final String REQUEST_ATTRIBUTE = "agentToolContext";

  public AgentToolContext {
    roles = List.copyOf(roles);
  }

  public AuthenticatedUser authenticatedUser() {
    return new AuthenticatedUser(userId, username, roles);
  }
}
