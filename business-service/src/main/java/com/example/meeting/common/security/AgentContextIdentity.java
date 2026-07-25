package com.example.meeting.common.security;

import java.util.List;

public record AgentContextIdentity(long userId, List<String> roles, String traceId, String runId) {

  public AgentContextIdentity {
    roles = List.copyOf(roles);
  }
}
