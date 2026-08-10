package com.example.meeting.organization.api;

import java.time.OffsetDateTime;

public record EmployeeItemView(
    long id,
    String username,
    String displayName,
    String email,
    Long departmentId,
    String departmentName,
    String role,
    String status,
    int version,
    OffsetDateTime createdAt,
    OffsetDateTime updatedAt) {}
