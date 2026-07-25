package com.example.meeting.common.security;

import java.util.List;

public record AuthenticatedUser(long userId, String username, List<String> roles) {

  public AuthenticatedUser {
    roles = List.copyOf(roles);
  }
}
