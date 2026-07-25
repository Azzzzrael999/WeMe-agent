package com.example.meeting.auth.api;

import java.util.List;

public record UserView(
    Long id,
    String username,
    String displayName,
    String email,
    Long departmentId,
    String departmentName,
    List<String> roles) {

  public UserView {
    roles = List.copyOf(roles);
  }
}
