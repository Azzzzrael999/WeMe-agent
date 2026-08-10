package com.example.meeting.organization.api;

public record EmployeeDirectoryItemView(
    long id, String displayName, Long departmentId, String departmentName) {}
