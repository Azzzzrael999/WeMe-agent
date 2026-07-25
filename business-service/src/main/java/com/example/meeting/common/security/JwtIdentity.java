package com.example.meeting.common.security;

import java.util.List;

public record JwtIdentity(long userId, String username, List<String> roles) {

  public JwtIdentity {
    roles = List.copyOf(roles);
  }
}
